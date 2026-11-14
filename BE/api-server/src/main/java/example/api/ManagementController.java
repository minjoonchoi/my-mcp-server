package example.api;

import java.util.Map;
import example.security.VerifiedCaller;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
public class ManagementController {
    private final AccessRepository repository;
    private final VerifiedCaller callers;
    public ManagementController(AccessRepository repository, VerifiedCaller callers) { this.repository = repository; this.callers = callers; }

    @GetMapping("/api/catalog/{entity}")
    @PreAuthorize("hasAuthority('SCOPE_access.read')")
    public Object list(@AuthenticationPrincipal Jwt jwt, @PathVariable String entity, @RequestParam Map<String, String> query) {
        noQuery(query); return repository.catalog(issuer(jwt), jwt.getSubject(), entity);
    }
    @GetMapping("/api/catalog/{entity}/{id}")
    @PreAuthorize("hasAuthority('SCOPE_access.read')")
    public Object detail(@AuthenticationPrincipal Jwt jwt, @PathVariable String entity, @PathVariable String id, @RequestParam Map<String, String> query) {
        noQuery(query); return repository.detail(issuer(jwt), jwt.getSubject(), entity, id);
    }
    @GetMapping("/api/me/requestable-roles")
    @PreAuthorize("hasAuthority('SCOPE_access.read')")
    public Object requestable(@AuthenticationPrincipal Jwt jwt, @RequestParam Map<String, String> query) {
        noQuery(query); return repository.requestable(issuer(jwt), jwt.getSubject());
    }
    @RequestMapping(value="/api/manage/users/{userId}/roles/{roleId}", method={RequestMethod.PUT, RequestMethod.DELETE})
    @PreAuthorize("hasAuthority('SCOPE_access.manage')")
    public Object userRole(@AuthenticationPrincipal Jwt jwt, @PathVariable String userId, @PathVariable String roleId,
            @RequestParam Map<String,String> query, @RequestBody(required=false) String body, HttpServletRequest request) {
        return repository.change(issuer(jwt), jwt.getSubject(), request.getMethod().equals("PUT") ? "assign_role_to_user" : "remove_role_from_user", validatedTargets(query,body,Map.of("userId",userId,"roleId",roleId)));
    }
    @RequestMapping(value="/api/manage/roles/{roleId}/policies/{policyId}", method={RequestMethod.PUT, RequestMethod.DELETE})
    @PreAuthorize("hasAuthority('SCOPE_access.manage')")
    public Object rolePolicy(@AuthenticationPrincipal Jwt jwt, @PathVariable String roleId, @PathVariable String policyId,
            @RequestParam Map<String,String> query, @RequestBody(required=false) String body, HttpServletRequest request) {
        return repository.change(issuer(jwt), jwt.getSubject(), request.getMethod().equals("PUT") ? "attach_policy_to_role" : "detach_policy_from_role", validatedTargets(query,body,Map.of("roleId",roleId,"policyId",policyId)));
    }
    @RequestMapping(value="/api/manage/policies/{policyId}/resources/{resourceId}", method={RequestMethod.PUT, RequestMethod.DELETE})
    @PreAuthorize("hasAuthority('SCOPE_access.manage')")
    public Object policyResource(@AuthenticationPrincipal Jwt jwt, @PathVariable String policyId, @PathVariable String resourceId,
            @RequestParam Map<String,String> query, @RequestBody(required=false) String body, HttpServletRequest request) {
        return repository.change(issuer(jwt), jwt.getSubject(), request.getMethod().equals("PUT") ? "attach_resource_to_policy" : "detach_resource_from_policy", validatedTargets(query,body,Map.of("policyId",policyId,"resourceId",resourceId)));
    }
    private String issuer(Jwt jwt) { return callers.resolve(jwt).issuer(); }
    private static void noQuery(Map<String,String> query) { if (!query.isEmpty()) throw new ApiProblem(400,"INVALID_ARGUMENT"); }
    private static Map<String,String> validatedTargets(Map<String,String> query,String body,Map<String,String> targets) {
        if(query.isEmpty() && (body==null || body.isBlank())) return targets;
        // Defer invalid-extra rejection until the repository has denied non-admin actors.
        var invalid=new java.util.HashMap<>(targets); invalid.put("__invalid_extra",""); return invalid;
    }
}
