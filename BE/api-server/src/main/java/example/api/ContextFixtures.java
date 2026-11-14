package example.api;

import java.util.List;
import java.util.Map;
import java.util.Set;
import example.contract.ContextAccessResponses.*;

/** Explicit fixture definitions matching local V6. Effective assignments are supplied live. */
final class ContextFixtures {
    static final String CUSTOMERS = "page-customers";
    static final String AUDIT = "page-audit";
    static final String ENDPOINT = "endpoint-customer-get";
    private record Target(String kind, String page, String endpoint, String field) {}

    private static final Map<String, Page> PAGES = Map.of(
            CUSTOMERS, new Page(CUSTOMERS, "backoffice", "/admin/customers", "Customer support"),
            AUDIT, new Page(AUDIT, "backoffice", "/admin/audit", "Customer audit"));
    private static final Map<String, Endpoint> ENDPOINTS = Map.of(
            ENDPOINT, new Endpoint(ENDPOINT, "customer-api", "GET", "/customers/{id}", "Get customer"));
    private static final Map<String, Field> FIELDS = Map.of(
            "field-customer-id-request", new Field("field-customer-id-request", ENDPOINT, "REQUEST", "PATH", "id", "Customer ID input"),
            "field-customer-name-response", new Field("field-customer-name-response", ENDPOINT, "RESPONSE", "BODY", "name", "Customer name output"),
            "field-customer-email-response", new Field("field-customer-email-response", ENDPOINT, "RESPONSE", "BODY", "email", "Customer email output"));
    private static final Set<ContextSnapshot.Binding> BINDINGS = Set.of(
            new ContextSnapshot.Binding(CUSTOMERS, ENDPOINT), new ContextSnapshot.Binding(AUDIT, ENDPOINT));
    private static final Map<String, Target> RULES = Map.of(
            "ctx-page-customers", new Target("PAGE", CUSTOMERS, null, null),
            "ctx-page-audit", new Target("PAGE", AUDIT, null, null),
            "ctx-endpoint-customers", new Target("ENDPOINT", CUSTOMERS, ENDPOINT, null),
            "ctx-endpoint-audit", new Target("ENDPOINT", AUDIT, ENDPOINT, null),
            "ctx-field-customers-id", new Target("FIELD", CUSTOMERS, ENDPOINT, "field-customer-id-request"),
            "ctx-field-customers-name", new Target("FIELD", CUSTOMERS, ENDPOINT, "field-customer-name-response"),
            "ctx-field-audit-id", new Target("FIELD", AUDIT, ENDPOINT, "field-customer-id-request"),
            "ctx-field-audit-name", new Target("FIELD", AUDIT, ENDPOINT, "field-customer-name-response"),
            "ctx-field-audit-email", new Target("FIELD", AUDIT, ENDPOINT, "field-customer-email-response"));

    static final Map<String, List<String>> ROLE_POLICIES = Map.of(
            "role-analyst", List.of("ctx-page-customers", "ctx-endpoint-customers", "ctx-field-customers-id", "ctx-field-customers-name", "ctx-field-audit-email"),
            "role-editor", List.of("ctx-page-audit", "ctx-endpoint-audit", "ctx-field-audit-id", "ctx-field-audit-name"));

    private ContextFixtures() {}

    static Set<String> policyIds() { return RULES.keySet(); }
    static boolean contextualPolicy(String id) { return RULES.containsKey(id); }

    static ContextSnapshot snapshot(List<Grant> currentAssignments) {
        List<ContextSnapshot.Rule> rules = currentAssignments.stream()
                .filter(grant -> RULES.containsKey(grant.policyId())).map(grant -> {
                    Target target = RULES.get(grant.policyId());
                    return new ContextSnapshot.Rule(target.kind(), target.page(), target.endpoint(), target.field(), grant);
                }).toList();
        return new ContextSnapshot(PAGES, ENDPOINTS, FIELDS, BINDINGS, rules);
    }
}
