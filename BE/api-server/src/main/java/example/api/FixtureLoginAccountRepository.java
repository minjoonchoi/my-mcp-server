package example.api;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Repository;

/** The fixture consumes the same generated hashes as MySQL; it has no built-in password. */
@Repository
@Profile("fixture")
public class FixtureLoginAccountRepository implements LoginAccountRepository {
    private record Entry(String userId, String issuer, String subject, String hash, boolean enabled) {
        @Override public String toString() { return "Entry[credentials=<redacted>]"; }
    }
    private static final Set<String> FIELDS = Set.of("username", "userId", "issuer", "subject", "passwordHash", "enabled");
    private final AccessDirectory directory;
    private final Map<String, Entry> entries;

    public FixtureLoginAccountRepository(AccessDirectory directory, ObjectMapper json,
            @Value("${auth.fixture-accounts}") Resource manifest) {
        this.directory = directory;
        Map<String, Entry> loaded;
        try (var input = manifest.getInputStream()) {
            byte[] body = input.readNBytes(65537);
            if (body.length > 65536) throw new IllegalArgumentException();
            var strict = json.copy().enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                    .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
            JsonNode root = strict.readTree(body);
            if (root == null || !root.isArray() || root.size() > 100) throw new IllegalArgumentException();
            loaded = new HashMap<>();
            var users = new HashSet<String>();
            for (JsonNode row : root) {
                if (!row.isObject() || row.size() != FIELDS.size()) throw new IllegalArgumentException();
                for (String field : FIELDS) if (!row.has(field)) throw new IllegalArgumentException();
                String username = text(row, "username"), userId = text(row, "userId");
                String issuer = text(row, "issuer"), subject = text(row, "subject"), hash = text(row, "passwordHash");
                if (!username.matches("[A-Za-z0-9_-]{1,64}") || !PasswordHashes.valid(hash)
                        || !row.get("enabled").isBoolean() || !users.add(userId)) throw new IllegalArgumentException();
                var identity = directory.loginIdentity(issuer, subject);
                if (identity == null || !identity.id().equals(userId)) throw new IllegalArgumentException();
                if (loaded.put(username, new Entry(userId, issuer, subject, hash, row.get("enabled").booleanValue())) != null)
                    throw new IllegalArgumentException();
            }
            loaded = Map.copyOf(loaded);
        } catch (Exception invalidOrMissing) { loaded = null; }
        entries = loaded;
    }

    @Override public Account find(String username) {
        if (entries == null) throw new ApiProblem(503, "AUTH_UNAVAILABLE");
        Entry entry = entries.get(username);
        if (entry == null) return null;
        var identity = directory.loginIdentity(entry.issuer(), entry.subject());
        if (identity == null || !identity.id().equals(entry.userId())) throw new ApiProblem(503, "AUTH_UNAVAILABLE");
        return new Account(identity.id(), entry.issuer(), entry.subject(), identity.name(), entry.hash(), entry.enabled(), identity.administrator());
    }

    private static String text(JsonNode row, String field) {
        if (!row.get(field).isTextual()) throw new IllegalArgumentException();
        return row.get(field).textValue();
    }
}
