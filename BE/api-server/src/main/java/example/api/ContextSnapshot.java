package example.api;

import java.util.List;
import java.util.Map;
import java.util.Set;
import example.contract.ContextAccessResponses.*;

/** One immutable request snapshot. Rules contain only the authenticated user's assignments. */
record ContextSnapshot(Map<String, Page> pages, Map<String, Endpoint> endpoints, Map<String, Field> fields,
                       Set<Binding> bindings, List<Rule> rules) {
    record Binding(String pageId, String endpointId) {}
    record Rule(String kind, String pageId, String endpointId, String fieldId, Grant grant) {}

    ContextSnapshot {
        pages = Map.copyOf(pages);
        endpoints = Map.copyOf(endpoints);
        fields = Map.copyOf(fields);
        bindings = Set.copyOf(bindings);
        rules = List.copyOf(rules);
        pages.forEach((key, p) -> ContextAccessContract.configuration(key.equals(p.id())
                && ContextAccessContract.isId(p.id()) && ContextAccessContract.isId(p.application())
                && ContextAccessContract.path(p.path()) && ContextAccessContract.name(p.name())));
        endpoints.forEach((key, e) -> ContextAccessContract.configuration(key.equals(e.id())
                && ContextAccessContract.isId(e.id()) && ContextAccessContract.isId(e.service())
                && ContextAccessContract.METHODS.contains(e.method())
                && ContextAccessContract.path(e.path()) && ContextAccessContract.name(e.name())));
        fields.forEach((key, f) -> ContextAccessContract.configuration(key.equals(f.id())
                && ContextAccessContract.isId(f.id()) && ContextAccessContract.isId(f.endpointId())
                && ContextAccessContract.location(f.direction(), f.location())
                && ContextAccessContract.printable(f.path(), 200) && ContextAccessContract.name(f.name())));
        for (Binding b : bindings) ContextAccessContract.configuration(ContextAccessContract.isId(b.pageId())
                && ContextAccessContract.isId(b.endpointId()));
        for (Rule r : rules) {
            boolean shape = switch (r.kind()) {
                case "PAGE" -> r.endpointId() == null && r.fieldId() == null;
                case "ENDPOINT" -> ContextAccessContract.isId(r.endpointId()) && r.fieldId() == null;
                case "FIELD" -> ContextAccessContract.isId(r.endpointId()) && ContextAccessContract.isId(r.fieldId());
                default -> false;
            };
            ContextAccessContract.configuration(shape && ContextAccessContract.isId(r.pageId())
                    && r.grant() != null && ContextAccessContract.isId(r.grant().roleId())
                    && ContextAccessContract.isId(r.grant().policyId()));
        }
    }
}
