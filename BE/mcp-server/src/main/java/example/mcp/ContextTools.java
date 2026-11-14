package example.mcp;

import java.math.BigInteger;
import java.util.Map;
import java.util.Set;
import example.contract.ContextAccessResponses.*;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;

/** Identity always comes from the authenticated request, never tool arguments. */
@Service
public class ContextTools {
    private final ContextApiClient api;

    public ContextTools(ContextApiClient api) { this.api = api; }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public PageList listMyPages(Map<String, Object> arguments) {
        keys(arguments, Set.of(), Set.of("after", "limit"));
        return api.pages(caller(), after(arguments), limit(arguments));
    }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public EndpointList listMyPageEndpoints(Map<String, Object> arguments) {
        keys(arguments, Set.of("pageId"), Set.of("after", "limit"));
        return api.endpoints(caller(), id(arguments, "pageId"), after(arguments), limit(arguments));
    }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public FieldList listMyEndpointFields(Map<String, Object> arguments) {
        keys(arguments, Set.of("pageId", "endpointId", "direction"), Set.of("after", "limit"));
        String direction = choice(arguments, "direction", Set.of("REQUEST", "RESPONSE"));
        return api.fields(caller(), id(arguments, "pageId"), id(arguments, "endpointId"), direction,
                after(arguments), limit(arguments));
    }

    @PreAuthorize("hasAuthority('SCOPE_authz.read')")
    public Decision checkMyContextAccess(Map<String, Object> arguments) {
        keys(arguments, Set.of("targetType", "pageId"), Set.of("endpointId", "fieldId"));
        String type = choice(arguments, "targetType", Set.of("PAGE", "ENDPOINT", "FIELD"));
        Set<String> required = switch (type) {
            case "PAGE" -> Set.of("targetType", "pageId");
            case "ENDPOINT" -> Set.of("targetType", "pageId", "endpointId");
            default -> Set.of("targetType", "pageId", "endpointId", "fieldId");
        };
        keys(arguments, required, Set.of());
        return api.check(caller(), type, id(arguments, "pageId"),
                type.equals("PAGE") ? null : id(arguments, "endpointId"),
                type.equals("FIELD") ? id(arguments, "fieldId") : null);
    }

    private static void keys(Map<String, Object> arguments, Set<String> required, Set<String> optional) {
        if (arguments == null || !arguments.keySet().containsAll(required)
                || arguments.keySet().stream().anyMatch(key -> !required.contains(key) && !optional.contains(key))) {
            throw invalid();
        }
    }

    private static String id(Map<String, Object> arguments, String key) {
        if (!(arguments.get(key) instanceof String value) || !value.matches("[A-Za-z0-9_-]{1,64}")) throw invalid();
        return value;
    }

    private static String choice(Map<String, Object> arguments, String key, Set<String> choices) {
        if (!(arguments.get(key) instanceof String value) || !choices.contains(value)) throw invalid();
        return value;
    }

    private static String after(Map<String, Object> arguments) {
        return arguments.containsKey("after") ? id(arguments, "after") : null;
    }

    private static int limit(Map<String, Object> arguments) {
        if (!arguments.containsKey("limit")) return 5;
        Object value = arguments.get("limit");
        if (!(value instanceof Byte || value instanceof Short || value instanceof Integer
                || value instanceof Long || value instanceof BigInteger)) throw invalid();
        BigInteger integer = new BigInteger(value.toString());
        if (integer.compareTo(BigInteger.ONE) < 0 || integer.compareTo(BigInteger.valueOf(5)) > 0) throw invalid();
        return integer.intValue();
    }

    private static IllegalArgumentException invalid() { return new IllegalArgumentException("INVALID_ARGUMENT"); }
    private static Jwt caller() { return (Jwt) SecurityContextHolder.getContext().getAuthentication().getPrincipal(); }
}
