package example.api;

import java.util.Map;
import java.util.Set;
import example.security.VerifiedCaller;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;
import example.contract.ContextAccessResponses.*;

@RestController
@RequestMapping("/api/me")
@PreAuthorize("hasAuthority('SCOPE_access.read')")
public class ContextAccessController {
    private final ContextAccessService service;
    private final VerifiedCaller callers;

    public ContextAccessController(ContextAccessService service, VerifiedCaller callers) { this.service = service; this.callers = callers; }

    @GetMapping("/pages")
    public PageList pages(@AuthenticationPrincipal Jwt caller, @RequestParam MultiValueMap<String, String> query) {
        Map<String, String> values = values(query, Set.of("after", "limit"), Set.of());
        Paging paging = paging(values);
        return service.pages(issuer(caller), caller.getSubject(), paging.after(), paging.limit());
    }

    @GetMapping("/pages/{pageId}/endpoints")
    public EndpointList endpoints(@AuthenticationPrincipal Jwt caller, @PathVariable String pageId,
            @RequestParam MultiValueMap<String, String> query) {
        ContextAccessContract.id(pageId);
        Map<String, String> values = values(query, Set.of("after", "limit"), Set.of());
        Paging paging = paging(values);
        return service.endpoints(issuer(caller), caller.getSubject(), pageId, paging.after(), paging.limit());
    }

    @GetMapping("/pages/{pageId}/endpoints/{endpointId}/fields")
    public FieldList fields(@AuthenticationPrincipal Jwt caller, @PathVariable String pageId,
            @PathVariable String endpointId, @RequestParam MultiValueMap<String, String> query) {
        ContextAccessContract.id(pageId);
        ContextAccessContract.id(endpointId);
        Map<String, String> values = values(query, Set.of("direction", "after", "limit"), Set.of("direction"));
        ContextAccessContract.direction(values.get("direction"));
        Paging paging = paging(values);
        return service.fields(issuer(caller), caller.getSubject(), pageId, endpointId,
                values.get("direction"), paging.after(), paging.limit());
    }

    @GetMapping("/context-access")
    public Decision check(@AuthenticationPrincipal Jwt caller, @RequestParam MultiValueMap<String, String> query) {
        Map<String, String> values = values(query,
                Set.of("targetType", "pageId", "endpointId", "fieldId"), Set.of("targetType", "pageId"));
        String type = values.get("targetType"), page = values.get("pageId");
        String endpoint = values.get("endpointId"), field = values.get("fieldId");
        ContextAccessContract.target(type, page, endpoint, field);
        return service.check(issuer(caller), caller.getSubject(), type, page, endpoint, field);
    }

    static Map<String, String> values(MultiValueMap<String, String> query, Set<String> allowed, Set<String> required) {
        if (!allowed.containsAll(query.keySet()) || !query.keySet().containsAll(required)
                || query.values().stream().anyMatch(v -> v == null || v.size() != 1
                    || v.getFirst() == null || v.getFirst().isEmpty())) throw ContextAccessContract.invalid();
        return query.toSingleValueMap();
    }

    private record Paging(String after, int limit) {}

    private static Paging paging(Map<String, String> values) {
        String raw = values.get("limit");
        if (raw != null && !raw.matches("[1-5]")) throw ContextAccessContract.invalid();
        int limit = raw == null ? ContextAccessContract.MAX_PAGE_SIZE : Integer.parseInt(raw);
        String after = values.get("after");
        ContextAccessContract.page(after, limit);
        return new Paging(after, limit);
    }

    private String issuer(Jwt caller) { return callers.resolve(caller).issuer(); }
}
