package example.api;

import java.util.Map;
import example.security.VerifiedCaller;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/me")
@PreAuthorize("hasAuthority('SCOPE_access.read')")
public class AccessController {
    private final AccessRepository directory;
    private final VerifiedCaller callers;
    public AccessController(AccessRepository directory, VerifiedCaller callers) { this.directory = directory; this.callers = callers; }

    @GetMapping
    public ResponseEntity<?> me(@AuthenticationPrincipal Jwt jwt, @RequestParam Map<String, String> params) {
        if (!params.isEmpty()) return invalid();
        var identity = callers.resolve(jwt);
        return ResponseEntity.ok(directory.access(identity.issuer(), identity.subject()));
    }

    @GetMapping("/resources/{resourceId}/access")
    public ResponseEntity<?> resource(@AuthenticationPrincipal Jwt jwt, @PathVariable String resourceId,
            @RequestParam Map<String, String> params) {
        if (!params.isEmpty() || !resourceId.matches("R[0-9]{3}")) return invalid();
        var identity = callers.resolve(jwt);
        return ResponseEntity.ok(directory.resourceAccess(identity.issuer(), identity.subject(), resourceId));
    }

    private ResponseEntity<?> invalid() { return ResponseEntity.badRequest().body(Map.of("code", "INVALID_ARGUMENT")); }

    @ExceptionHandler(org.springframework.security.access.AccessDeniedException.class)
    ResponseEntity<?> denied() { return ResponseEntity.status(403).body(Map.of("code", "FORBIDDEN")); }
}
