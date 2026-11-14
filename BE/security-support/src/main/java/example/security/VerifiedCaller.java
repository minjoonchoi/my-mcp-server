package example.security;

import java.util.Map;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.oauth2.jwt.Jwt;

/** Resolves only a JWT already verified by Spring Security's pinned direct/delegated decoders. */
public final class VerifiedCaller {
    private final String userIssuer;
    private final String delegationIssuer;

    public VerifiedCaller(String userIssuer, String delegationIssuer) {
        this.userIssuer = userIssuer;
        this.delegationIssuer = delegationIssuer;
    }

    public record Identity(String issuer, String subject) {}

    public Identity resolve(Jwt verified) {
        if (verified == null || verified.getSubject() == null || verified.getSubject().isBlank()) throw denied();
        String issuer = verified.getIssuer() == null ? null : verified.getIssuer().toString();
        if (userIssuer.equals(issuer) && !verified.hasClaim("act") && !verified.hasClaim("original_iss"))
            return new Identity(userIssuer, verified.getSubject());
        if (delegationIssuer.equals(issuer) && userIssuer.equals(verified.getClaimAsString("original_iss"))
                && verified.getClaims().get("act") instanceof Map<?, ?> actor && "access-mcp".equals(actor.get("sub")))
            return new Identity(userIssuer, verified.getSubject());
        throw denied();
    }

    private static AccessDeniedException denied() { return new AccessDeniedException("FORBIDDEN"); }
}
