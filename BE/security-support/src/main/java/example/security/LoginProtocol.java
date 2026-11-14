package example.security;

import java.nio.charset.StandardCharsets;
import java.util.Set;
import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.contract.AuthResponses.*;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.util.UriUtils;

/** Strict bounded login wire parsing shared by the API and its fixed MCP relay. */
public final class LoginProtocol {
    public static final int MAX_REQUEST_BYTES = 4096;
    public static final int MAX_RESPONSE_BYTES = 16384;
    private static final ObjectMapper JSON = new ObjectMapper(JsonFactory.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build());

    private LoginProtocol() {}

    public record Credentials(String username, String password) {
        @Override public String toString() { return "Credentials[password=<redacted>]"; }
    }
    public static final class InvalidRequest extends RuntimeException {
        public InvalidRequest() { super("INVALID_ARGUMENT", null, false, false); }
    }

    public static Credentials credentials(byte[] body) {
        try {
            JsonNode root = read(body, MAX_REQUEST_BYTES);
            exact(root, Set.of("username", "password"));
            JsonNode username = root.get("username"), password = root.get("password");
            if (!username.isTextual() || !username.textValue().matches("[A-Za-z0-9_-]{1,64}")
                    || !password.isTextual() || password.textValue().isEmpty() || password.textValue().length() > 256)
                throw new InvalidRequest();
            return new Credentials(username.textValue(), password.textValue());
        } catch (Exception ignored) { throw new InvalidRequest(); }
    }

    public static LoginResult result(byte[] body) {
        try {
            JsonNode root = read(body, MAX_RESPONSE_BYTES);
            exact(root, Set.of("tokenType", "expiresIn", "mcpAccessToken", "apiAccessToken", "user"));
            if (!root.path("tokenType").isTextual() || !"Bearer".equals(root.path("tokenType").textValue())
                    || !root.path("expiresIn").isIntegralNumber() || !root.path("expiresIn").canConvertToLong())
                throw new InvalidRequest();
            long ttl = root.path("expiresIn").longValue();
            if (ttl < 60 || ttl > 900) throw new InvalidRequest();
            JsonNode user = root.get("user");
            exact(user, Set.of("id", "name"));
            if (!user.path("id").isTextual() || !user.path("id").textValue().matches("[A-Za-z0-9_-]{1,64}")
                    || !user.path("name").isTextual() || user.path("name").textValue().isBlank()
                    || user.path("name").textValue().length() > 200
                    || user.path("name").textValue().codePoints().anyMatch(Character::isISOControl)) throw new InvalidRequest();
            return new LoginResult("Bearer", ttl, token(root.get("mcpAccessToken")), token(root.get("apiAccessToken")),
                    new LoginUser(user.path("id").textValue(), user.path("name").textValue()));
        } catch (Exception ignored) { throw new InvalidRequest(); }
    }

    public static boolean isLoginPath(HttpServletRequest request, boolean api) {
        if (!"POST".equals(request.getMethod())) return false;
        try {
            String path = UriUtils.decode(request.getRequestURI(), StandardCharsets.UTF_8);
            return (api ? "/api/auth/login" : "/auth/login").equals(path);
        } catch (IllegalArgumentException invalid) { return false; }
    }

    private static JsonNode read(byte[] body, int limit) throws Exception {
        if (body == null || body.length == 0 || body.length > limit) throw new InvalidRequest();
        try (var parser = JSON.createParser(body)) {
            JsonNode node = JSON.readTree(parser);
            if (parser.nextToken() != null) throw new InvalidRequest();
            return node;
        }
    }

    private static void exact(JsonNode node, Set<String> fields) {
        if (node == null || !node.isObject() || node.size() != fields.size()) throw new InvalidRequest();
        for (String field : fields) if (!node.has(field)) throw new InvalidRequest();
    }

    private static String token(JsonNode node) {
        if (node == null || !node.isTextual()) throw new InvalidRequest();
        String value = node.textValue();
        if (value.length() > 8192 || !value.matches("[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+")) throw new InvalidRequest();
        return value;
    }
}
