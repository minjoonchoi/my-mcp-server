package example.contract;

/** API-owned login response; tokens are never suitable for logs. */
public final class AuthResponses {
    private AuthResponses() {}

    public record LoginUser(String id, String name) {}
    public record LoginResult(String tokenType, long expiresIn, String mcpAccessToken,
                              String apiAccessToken, LoginUser user) {
        @Override public String toString() { return "LoginResult[tokens=<redacted>]"; }
    }
}
