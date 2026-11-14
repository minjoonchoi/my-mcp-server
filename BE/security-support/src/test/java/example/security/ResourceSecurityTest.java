package example.security;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.nimbusds.jose.*;
import com.nimbusds.jose.crypto.RSASSASigner;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.security.oauth2.jwt.*;
import static org.assertj.core.api.Assertions.*;

class ResourceSecurityTest {
    private static final String USER = "https://demo-issuer.invalid", DELEGATION = "https://demo-delegation.invalid";
    private static final ObjectMapper JSON = new ObjectMapper();
    private static KeyPair userKey, delegationKey;

    @BeforeAll static void keys() throws Exception {
        var generator = KeyPairGenerator.getInstance("RSA"); generator.initialize(2048);
        userKey = generator.generateKeyPair(); delegationKey = generator.generateKeyPair();
    }

    @Test void directAndDelegatedResolveSameVerifiedIdentityUsingDifferentKeys() throws Exception {
        var decoder = decoder();
        var resolver = new VerifiedCaller(USER, DELEGATION);
        var direct = decoder.decode(sign(userKey, claims(false)));
        var delegated = decoder.decode(sign(delegationKey, claims(true)));
        assertThat(resolver.resolve(direct)).isEqualTo(new VerifiedCaller.Identity(USER, "local-id-demo"));
        assertThat(resolver.resolve(delegated)).isEqualTo(resolver.resolve(direct));
    }

    @Test void issuerSelectionNeverFallsBackToAnotherKeyOrAudience() throws Exception {
        var decoder = decoder();
        assertRejected(decoder, delegationKey, claims(false));
        assertRejected(decoder, userKey, claims(true));
        var unknown = claims(false); unknown.put("iss", "https://untrusted.invalid"); assertRejected(decoder, userKey, unknown);
        var mcp = claims(false); mcp.put("aud", List.of("access-mcp")); assertRejected(decoder, userKey, mcp);
        var mcpDecoder = ResourceSecurity.pinned(JSON, (RSAPublicKey) userKey.getPublic(), USER, "access-mcp", false, false, USER);
        assertRejected(mcpDecoder, userKey, claims(false));
        assertThatThrownBy(() -> decoder.decode("not.jwt")).isInstanceOf(JwtException.class);
    }

    @Test void directRejectsDelegationFieldsWrongRawTypesAndUnboundedLifetime() throws Exception {
        var decoder = decoder();
        for (var entry : Map.<String, Object>of("act", Map.of("sub", "access-mcp"), "original_iss", USER,
                "sub", 42, "iat", Long.toString(Instant.now().getEpochSecond()), "exp", Long.toString(Instant.now().plusSeconds(60).getEpochSecond()),
                "nbf", "1", "scope", List.of("access.read")).entrySet()) {
            var changed = claims(false); changed.put(entry.getKey(), entry.getValue()); assertRejected(decoder, userKey, changed);
        }
        for (String required : List.of("iss", "aud", "sub", "exp", "iat")) {
            var changed = claims(false); changed.remove(required); assertRejected(decoder, userKey, changed);
        }
        var tooLong = claims(false); tooLong.put("exp", ((Number) tooLong.get("iat")).longValue() + 901); assertRejected(decoder, userKey, tooLong);
        var explicitNull = claims(false); explicitNull.put("act", null); assertRejected(decoder, userKey, explicitNull);
    }

    @Test void delegationRequiresPinnedOriginalActorAndShortLifetime() throws Exception {
        var decoder = decoder();
        for (var entry : Map.<String, Object>of("original_iss", USER + " ", "act", Map.of("sub", "different"),
                "exp", Instant.now().plusSeconds(120).getEpochSecond()).entrySet()) {
            var changed = claims(true); changed.put(entry.getKey(), entry.getValue()); assertRejected(decoder, delegationKey, changed);
        }
        for (String claim : List.of("original_iss", "act", "iat")) {
            var changed = claims(true); changed.remove(claim); assertRejected(decoder, delegationKey, changed);
        }
    }

    private static JwtDecoder decoder() throws Exception {
        return new ResourceSecurity().jwtDecoder(JSON, publicResource(delegationKey), DELEGATION, "access-api", true, USER, publicResource(userKey));
    }
    private static ByteArrayResource publicResource(KeyPair pair) {
        return new ByteArrayResource(("-----BEGIN PUBLIC KEY-----\n" + Base64.getMimeEncoder().encodeToString(pair.getPublic().getEncoded()) + "\n-----END PUBLIC KEY-----\n").getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    }
    private static Map<String, Object> claims(boolean delegated) {
        long now = Instant.now().getEpochSecond();
        var claims = new LinkedHashMap<String, Object>();
        claims.put("iss", delegated ? DELEGATION : USER); claims.put("sub", "local-id-demo"); claims.put("aud", List.of("access-api"));
        claims.put("scope", "access.read"); claims.put("iat", now); claims.put("nbf", now - 1); claims.put("exp", now + (delegated ? 60 : 900));
        if (delegated) { claims.put("act", Map.of("sub", "access-mcp")); claims.put("original_iss", USER); }
        return claims;
    }
    private static String sign(KeyPair pair, Map<String, Object> claims) throws Exception {
        var token = new JWSObject(new JWSHeader(JWSAlgorithm.RS256), new Payload(JSON.writeValueAsString(claims)));
        token.sign(new RSASSASigner((RSAPrivateKey) pair.getPrivate())); return token.serialize();
    }
    private static void assertRejected(JwtDecoder decoder, KeyPair pair, Map<String, Object> claims) throws Exception {
        String value = sign(pair, claims);
        assertThatThrownBy(() -> decoder.decode(value)).isInstanceOf(JwtException.class);
    }
}
