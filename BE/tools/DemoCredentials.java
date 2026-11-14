import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.*;
import java.time.Instant;
import java.util.*;

/** Development credentials. Only --fixtures persists a private test issuer for same-key refresh. */
public class DemoCredentials {
    private static final String ISSUER = "https://demo-issuer.invalid";
    private static boolean refresh;
    private static final Base64.Encoder B64 = Base64.getUrlEncoder().withoutPadding();
    public static void main(String[] args) throws Exception {
        if (args.length < 1 || args.length > 2 || (args.length == 2 && !Set.of("--fixtures","--refresh-fixtures").contains(args[1])))
            throw new IllegalArgumentException("Usage: java tools/DemoCredentials.java OUTPUT_DIR [--fixtures|--refresh-fixtures]");
        Path directory = Path.of(args[0]);
        Files.createDirectories(directory);
        Files.setPosixFilePermissions(directory, PosixFilePermissions.fromString("rwx------"));
        refresh = args.length == 2 && args[1].equals("--refresh-fixtures");
        if (!refresh && Files.exists(directory.resolve("issuer-public.pem")))
            throw new IllegalArgumentException("Use a new directory; credentials are never replaced");
        var generator = KeyPairGenerator.getInstance("RSA"); generator.initialize(2048);
        KeyPair clientKey, delegatedKey;
        if (refresh) {
            clientKey=readPair(directory,"issuer-public.pem","fixture-issuer-private.pem");
            delegatedKey=readPair(directory,"delegation-public.pem","delegation-private.pem");
        } else {
            clientKey=generator.generateKeyPair(); delegatedKey=generator.generateKeyPair();
            write(directory,"issuer-public.pem",pem("PUBLIC",clientKey.getPublic()));
            write(directory,"delegation-public.pem",pem("PUBLIC",delegatedKey.getPublic()));
            write(directory,"delegation-private.pem",pem("PRIVATE",delegatedKey.getPrivate()));
            if(args.length==2) write(directory,"fixture-issuer-private.pem",pem("PRIVATE",clientKey.getPrivate()));
        }
        long now = Instant.now().getEpochSecond();
        for (String user : List.of("alice", "bob", "carol", "local-id-demo", "admin")) {
            var client=claims(false,user,now);
            if(user.equals("admin")) client.put("scope","mcp.connect authz.read authz.manage");
            write(directory,"mcp-"+user+".jwt",token(clientKey,client,"RS256"));
        }
        if (args.length == 2) {
            var wrongKey = generator.generateKeyPair();
            for (boolean delegated : List.of(false, true)) {
                String prefix = delegated ? "api-" : "mcp-";
                KeyPair key = delegated ? delegatedKey : clientKey;
                var base = claims(delegated, "alice", now);
                write(directory, prefix + "valid.jwt", token(key, base, "RS256"));
                for (String field : List.of("aud", "iss", "exp", "sub")) {
                    var changed = new LinkedHashMap<>(base); changed.remove(field);
                    write(directory, prefix + "no-" + field + ".jwt", token(key, changed, "RS256"));
                }
                for (String variant : List.of("wrong-audience", "wrong-issuer", "expired", "future", "blank-subject", "typed-subject", "no-scope")) {
                    var changed = new LinkedHashMap<>(base);
                    switch (variant) {
                        case "wrong-audience" -> changed.put("aud", List.of("other-service"));
                        case "wrong-issuer" -> changed.put("iss", "https://untrusted.invalid");
                        case "expired" -> changed.put("exp", now - 10);
                        case "future" -> changed.put("nbf", now + 30);
                        case "blank-subject" -> changed.put("sub", " ");
                        case "typed-subject" -> changed.put("sub", 42);
                        default -> changed.put("scope", "");
                    }
                    write(directory, prefix + variant + ".jwt", token(key, changed, "RS256"));
                }
                var optional = new LinkedHashMap<>(base); optional.remove("nbf");
                write(directory, prefix + "no-nbf.jwt", token(key, optional, "RS256"));
                write(directory, prefix + "forged.jwt", token(wrongKey, base, "RS256"));
                write(directory, prefix + "wrong-alg.jwt", token(key, base, "RS512"));
                write(directory, prefix + "unknown.jwt", token(key, claims(delegated, "unknown-user", now), "RS256"));
            }
            var limited = claims(false, "alice", now); limited.put("scope", "mcp.connect");
            write(directory, "mcp-connect-only.jwt", token(clientKey, limited, "RS256"));
            var shortLived = claims(false, "alice", now); shortLived.put("exp", now + 45);
            write(directory, "mcp-near-expiry.jwt", token(clientKey, shortLived, "RS256"));
            var manipulated = claims(false, "carol", now);
            manipulated.put("scope", "mcp.connect authz.read access.read admin");
            manipulated.put("act", Map.of("sub", "alice")); manipulated.put("original_iss", "https://forged.invalid");
            manipulated.put("userId", "alice"); manipulated.put("grants", List.of("R100", "R400"));
            write(directory, "mcp-manipulated.jwt", token(clientKey, manipulated, "RS256"));
            for (String variant : List.of("wrong-actor", "typed-actor", "no-actor", "wrong-original", "typed-original", "no-original", "long-lived")) {
                var changed = claims(true, "alice", now);
                switch (variant) {
                    case "wrong-actor" -> changed.put("act", Map.of("sub", "other-service"));
                    case "typed-actor" -> changed.put("act", "access-mcp");
                    case "no-actor" -> changed.remove("act");
                    case "wrong-original" -> changed.put("original_iss", "https://forged.invalid");
                    case "typed-original" -> changed.put("original_iss", List.of(ISSUER));
                    case "no-original" -> changed.remove("original_iss");
                    case "long-lived" -> changed.put("exp", now + 3600);
                }
                write(directory, "api-" + variant + ".jwt", token(delegatedKey, changed, "RS256"));
            }
            for (String user : List.of("bob", "carol", "local-id-demo"))
                write(directory, "api-" + user + ".jwt", token(delegatedKey, claims(true, user, now), "RS256"));
            for (var entry : Map.of("admin-read-only","mcp.connect authz.read", "admin-manage-only","mcp.connect authz.manage", "alice-manage","mcp.connect authz.read authz.manage", "carol-manage","mcp.connect authz.read authz.manage").entrySet()) {
                String subject=entry.getKey().split("-")[0]; var c=claims(false,subject,now); c.put("scope",entry.getValue());
                write(directory,"mcp-"+entry.getKey()+".jwt",token(clientKey,c,"RS256"));
            }
            for(String name:List.of("admin-read","admin-manage","alice-manage","carol-manage")) {
                var c=claims(true,name.split("-")[0],now); c.put("scope",name.endsWith("manage")?"access.manage":"access.read");
                write(directory,"api-"+name+".jwt",token(delegatedKey,c,"RS256"));
            }
            var unknownManage=claims(false,"unknown-user",now); unknownManage.put("scope","mcp.connect authz.manage");
            write(directory,"mcp-unknown-manage.jwt",token(clientKey,unknownManage,"RS256"));
            var unknownApi=claims(true,"unknown-user",now); unknownApi.put("scope","access.manage");
            write(directory,"api-unknown-manage.jwt",token(delegatedKey,unknownApi,"RS256"));
            for (var variant : Map.of("case", "ALICE", "accent", "álice", "space", "alice ").entrySet()) {
                write(directory, "mcp-sub-" + variant.getKey() + ".jwt", token(clientKey, claims(false, variant.getValue(), now), "RS256"));
                write(directory, "api-sub-" + variant.getKey() + ".jwt", token(delegatedKey, claims(true, variant.getValue(), now), "RS256"));
            }
            for (var variant : Map.of("case", "https://DEMO-ISSUER.invalid", "space", ISSUER + " ").entrySet()) {
                var changed = claims(true, "alice", now); changed.put("original_iss", variant.getValue());
                write(directory, "api-original-" + variant.getKey() + ".jwt", token(delegatedKey, changed, "RS256"));
            }
        }
        System.out.println(refresh ? "Development fixtures refreshed under unchanged keys." : "Development credentials created; keep private key files backend-only.");
    }
    private static Map<String, Object> claims(boolean delegated, String subject, long now) {
        var claims = new LinkedHashMap<String, Object>();
        claims.put("iss", delegated ? "https://demo-delegation.invalid" : ISSUER);
        claims.put("aud", List.of(delegated ? "access-api" : "access-mcp")); claims.put("sub", subject);
        claims.put("scope", delegated ? "access.read" : "mcp.connect authz.read");
        claims.put("iat", now); claims.put("nbf", now - 1); claims.put("exp", now + (delegated ? 60 : 3600));
        if (delegated) { claims.put("act", Map.of("sub", "access-mcp")); claims.put("original_iss", ISSUER); }
        return claims;
    }
    private static String pem(String type, Key key) {
        return "-----BEGIN " + type + " KEY-----\n" + Base64.getMimeEncoder(64, new byte[]{'\n'})
                .encodeToString(key.getEncoded()) + "\n-----END " + type + " KEY-----\n";
    }
    private static void write(Path directory, String filename, String value) throws Exception {
        Path file = directory.resolve(filename);
        if(refresh) {
            if(!filename.endsWith(".jwt")) throw new IllegalStateException("Refresh must not replace keys");
            Path temp=Files.createTempFile(directory,"token-",".tmp",PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
            try { Files.writeString(temp,value,StandardCharsets.UTF_8); Files.move(temp,file,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE); }
            finally { Files.deleteIfExists(temp); }
        } else {
            Files.createFile(file, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
            Files.writeString(file, value, StandardCharsets.UTF_8);
        }
    }
    private static KeyPair readPair(Path directory,String publicName,String privateName) throws Exception {
        var factory=KeyFactory.getInstance("RSA");
        var pub=factory.generatePublic(new java.security.spec.X509EncodedKeySpec(readPem(directory.resolve(publicName))));
        var priv=factory.generatePrivate(new java.security.spec.PKCS8EncodedKeySpec(readPem(directory.resolve(privateName))));
        return new KeyPair(pub,priv);
    }
    private static byte[] readPem(Path path) throws Exception {
        String text=Files.readString(path).replaceAll("-----[^-]+-----","").replaceAll("\\s","");
        return Base64.getDecoder().decode(text);
    }
    private static String token(KeyPair key, Map<String, Object> claims, String algorithm) throws Exception {
        String unsigned = B64.encodeToString(json(Map.of("alg", algorithm, "typ", "JWT")).getBytes(StandardCharsets.UTF_8))
                + "." + B64.encodeToString(json(claims).getBytes(StandardCharsets.UTF_8));
        var signature = Signature.getInstance(algorithm.equals("RS256") ? "SHA256withRSA" : "SHA512withRSA");
        signature.initSign(key.getPrivate()); signature.update(unsigned.getBytes(StandardCharsets.US_ASCII));
        return unsigned + "." + B64.encodeToString(signature.sign());
    }
    private static String json(Object value) {
        if (value instanceof String s) return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
        if (value instanceof Number || value instanceof Boolean) return value.toString();
        if (value instanceof List<?> list) return "[" + String.join(",", list.stream().map(DemoCredentials::json).toList()) + "]";
        if (value instanceof Map<?, ?> map) return "{" + String.join(",", map.entrySet().stream()
                .map(e -> json(e.getKey()) + ":" + json(e.getValue())).toList()) + "}";
        throw new IllegalArgumentException("Unsupported development claim type");
    }
}
