package example.api;

import java.nio.charset.StandardCharsets;
import java.security.Key;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.security.LoginProtocol;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import static org.assertj.core.api.Assertions.*;

class LoginTest {
    private static final String ISSUER = "https://demo-issuer.invalid", PASSWORD = "  exact Päß word  ";
    private static final ObjectMapper JSON = new ObjectMapper();
    private static String hash;
    private static KeyPair keys;

    @BeforeAll static void credentials() throws Exception {
        hash = PasswordHashes.hash(PASSWORD);
        var generator = KeyPairGenerator.getInstance("RSA"); generator.initialize(2048); keys = generator.generateKeyPair();
    }

    @Test void fixedHashFormatUsesRandomSaltAndExactPassword() {
        assertThat(hash).startsWith("pbkdf2-sha256$600000$");
        assertThat(PasswordHashes.valid(hash)).isTrue();
        assertThat(PasswordHashes.matches(PASSWORD, hash)).isTrue();
        assertThat(PasswordHashes.matches(PASSWORD.trim(), hash)).isFalse();
        assertThat(PasswordHashes.matches("  exact pass word  ", hash)).isFalse();
        assertThat(PasswordHashes.hash(PASSWORD)).isNotEqualTo(hash);
    }

    @Test void hashParserRejectsUnboundedWorkAndNoncanonicalEncoding() {
        for (String invalid : List.of(hash.replace("600000", "600001"), hash.replace("600000", "999999999"),
                hash.replace("pbkdf2-sha256", "pbkdf2-sha1"), hash + "=", hash + "$extra", "bad", "")) {
            assertThat(PasswordHashes.valid(invalid)).isFalse();
            assertThat(PasswordHashes.matches(PASSWORD, invalid)).isFalse();
        }
        assertThat(PasswordHashes.valid(null)).isFalse();
    }

    @Test void successfulLoginUsesMappedSubjectAndAudienceSpecificScopesAndCommonTtl() {
        var issuer = issuer(123);
        var account = account(true, false);
        var service = new LoginService(name -> name.equals("local-id-demo") ? account : null, issuer, JSON, ISSUER);
        var result = service.login(new LoginProtocol.Credentials("local-id-demo", PASSWORD));
        assertThat(result.user().id()).isEqualTo("U900");
        assertThat(result.expiresIn()).isEqualTo(123);
        var decoder = NimbusJwtDecoder.withPublicKey((RSAPublicKey) keys.getPublic()).build();
        var mcp = decoder.decode(result.mcpAccessToken());
        var api = decoder.decode(result.apiAccessToken());
        assertThat(mcp.getSubject()).isEqualTo("local-id-demo");
        assertThat(api.getSubject()).isEqualTo("local-id-demo");
        assertThat(mcp.getAudience()).containsExactly("access-mcp");
        assertThat(api.getAudience()).containsExactly("access-api");
        assertThat(mcp.getClaimAsString("scope")).isEqualTo("mcp.connect authz.read");
        assertThat(api.getClaimAsString("scope")).isEqualTo("access.read");
        assertThat(api.getClaims()).doesNotContainKeys("act", "original_iss", "roles", "grants");
        assertThat(api.getIssuedAt()).isEqualTo(mcp.getIssuedAt());
        assertThat(api.getExpiresAt()).isEqualTo(mcp.getExpiresAt());
        assertThat(java.time.Duration.between(api.getIssuedAt(), api.getExpiresAt()).getSeconds()).isEqualTo(result.expiresIn());
        assertThat(result.toString()).doesNotContain(result.apiAccessToken(), result.mcpAccessToken());
    }

    @Test void unknownWrongDisabledAndInvalidStoredHashHaveSameFailure() {
        var accounts = Map.of("enabled", account(true, false), "disabled", account(false, true),
                "malformed", new LoginAccountRepository.Account("U900", ISSUER, "local-id-demo", "사용자", "not-a-hash", true, false));
        var service = new LoginService(accounts::get, issuer(900), JSON, ISSUER);
        fails(() -> service.login(new LoginProtocol.Credentials("unknown", PASSWORD)), 401, "INVALID_CREDENTIALS");
        fails(() -> service.login(new LoginProtocol.Credentials("enabled", "wrong")), 401, "INVALID_CREDENTIALS");
        fails(() -> service.login(new LoginProtocol.Credentials("disabled", PASSWORD)), 401, "INVALID_CREDENTIALS");
        fails(() -> service.login(new LoginProtocol.Credentials("malformed", PASSWORD)), 401, "INVALID_CREDENTIALS");
    }

    @Test void onlyCurrentAdminStateAddsManageScopeAndDoesNotAlterIdentity() {
        var state = new java.util.concurrent.atomic.AtomicBoolean(true);
        var service = new LoginService(name -> account(true, state.get()), issuer(900), JSON, ISSUER);
        var decoder = NimbusJwtDecoder.withPublicKey((RSAPublicKey) keys.getPublic()).build();
        var admin = service.login(new LoginProtocol.Credentials("account", PASSWORD));
        assertThat(decoder.decode(admin.mcpAccessToken()).getClaimAsString("scope")).isEqualTo("mcp.connect authz.read authz.manage");
        assertThat(decoder.decode(admin.apiAccessToken()).getClaimAsString("scope")).isEqualTo("access.read access.manage");
        state.set(false);
        var ordinary = service.login(new LoginProtocol.Credentials("account", PASSWORD));
        assertThat(decoder.decode(ordinary.apiAccessToken()).getClaimAsString("scope")).isEqualTo("access.read");
    }

    @Test void missingSigningKeyOrWrongMappedIssuerFailsClosedAndTtlConfigIsBounded() {
        var missing = new LoginTokenIssuer(ISSUER, 900, new ByteArrayResource(new byte[0]), pem("PUBLIC", keys.getPublic()));
        fails(() -> missing.issue(account(true, false)), 503, "AUTH_UNAVAILABLE");
        var wrongIdentity = new LoginAccountRepository.Account("U900", "https://other.invalid", "local-id-demo", "사용자", hash, true, false);
        var service = new LoginService(name -> wrongIdentity, issuer(900), JSON, ISSUER);
        fails(() -> service.login(new LoginProtocol.Credentials("account", PASSWORD)), 503, "AUTH_UNAVAILABLE");
        for (long ttl : new long[] {0, 59, 901, Long.MAX_VALUE}) assertThatThrownBy(() -> issuer(ttl)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test void fixtureRequiresTrustedMappingSupportsDisabledAndUsesLiveDomainIdentity() throws Exception {
        var directory = new AccessDirectory();
        var record = new java.util.LinkedHashMap<String, Object>(Map.of("username", "different-login", "userId", "U900", "issuer", ISSUER,
                "subject", "local-id-demo", "passwordHash", hash, "enabled", false));
        var repository = fixture(directory, JSON.writeValueAsBytes(List.of(record)));
        assertThat(repository.find("different-login").userId()).isEqualTo("U900");
        assertThat(repository.find("different-login").subject()).isEqualTo("local-id-demo");
        assertThat(repository.find("different-login").enabled()).isFalse();
        assertThat(repository.find("DIFFERENT-LOGIN")).isNull();
        record.put("userId", "alice");
        var invalid = fixture(directory, JSON.writeValueAsBytes(List.of(record)));
        fails(() -> invalid.find("different-login"), 503, "AUTH_UNAVAILABLE");
    }

    @Test void fixtureMissingDuplicateExtraAndMalformedManifestsNeverFallback() throws Exception {
        var directory = new AccessDirectory();
        for (String invalid : List.of("", "null", "{}", "[]{}", "[{\"username\":\"alice\",\"username\":\"bob\"}]")) {
            var repository = fixture(directory, invalid.getBytes(StandardCharsets.UTF_8));
            fails(() -> repository.find("alice"), 503, "AUTH_UNAVAILABLE");
        }
        var record = Map.of("username", "alice", "userId", "alice", "issuer", ISSUER, "subject", "alice", "passwordHash", hash, "enabled", true);
        var duplicate = fixture(directory, JSON.writeValueAsBytes(List.of(record, record)));
        fails(() -> duplicate.find("alice"), 503, "AUTH_UNAVAILABLE");
        var extra = new java.util.LinkedHashMap<String, Object>(record); extra.put("role", "admin");
        var unknownField = fixture(directory, JSON.writeValueAsBytes(List.of(extra)));
        fails(() -> unknownField.find("alice"), 503, "AUTH_UNAVAILABLE");
    }

    private static LoginAccountRepository.Account account(boolean enabled, boolean admin) {
        return new LoginAccountRepository.Account("U900", ISSUER, "local-id-demo", "사용자", hash, enabled, admin);
    }
    private static LoginTokenIssuer issuer(long ttl) { return new LoginTokenIssuer(ISSUER, ttl, pem("PRIVATE", keys.getPrivate()), pem("PUBLIC", keys.getPublic())); }
    private static ByteArrayResource pem(String label, Key key) {
        return new ByteArrayResource(("-----BEGIN " + label + " KEY-----\n" + Base64.getMimeEncoder().encodeToString(key.getEncoded())
                + "\n-----END " + label + " KEY-----\n").getBytes(StandardCharsets.US_ASCII));
    }
    private static FixtureLoginAccountRepository fixture(AccessDirectory directory, byte[] body) { return new FixtureLoginAccountRepository(directory, JSON, new ByteArrayResource(body)); }
    private static void fails(Runnable action, int status, String code) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiProblem.class, error -> assertThat(error.status).isEqualTo(status)).hasMessage(code);
    }
}
