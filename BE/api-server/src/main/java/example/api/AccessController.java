package example.api;

import java.util.Map;
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
    public AccessController(AccessRepository directory) { this.directory = directory; }

    @GetMapping
    public ResponseEntity<?> me(@AuthenticationPrincipal Jwt jwt, @RequestParam Map<String, String> params) {
        if (!params.isEmpty()) return invalid();
        return ResponseEntity.ok(directory.access(jwt.getClaimAsString("original_iss"), jwt.getSubject()));
    }

    @GetMapping("/resources/{resourceId}/access")
    public ResponseEntity<?> resource(@AuthenticationPrincipal Jwt jwt, @PathVariable String resourceId,
            @RequestParam Map<String, String> params) {
        if (!params.isEmpty() || !resourceId.matches("R[0-9]{3}")) return invalid();
        return ResponseEntity.ok(directory.resourceAccess(jwt.getClaimAsString("original_iss"), jwt.getSubject(), resourceId));
    }

    private ResponseEntity<?> invalid() { return ResponseEntity.badRequest().body(Map.of("code", "INVALID_ARGUMENT")); }

    @ExceptionHandler(org.springframework.security.access.AccessDeniedException.class)
    ResponseEntity<?> denied() { return ResponseEntity.status(403).body(Map.of("code", "FORBIDDEN")); }
}
