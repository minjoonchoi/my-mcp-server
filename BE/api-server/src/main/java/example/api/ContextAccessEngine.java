package example.api;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.function.Function;
import example.contract.ContextAccessResponses.*;

/** Pure, fail-closed evaluation of one page context; no HTTP or mutable security state. */
final class ContextAccessEngine {
    private static final Comparator<Grant> GRANT_ORDER = Comparator.comparing(Grant::roleId).thenComparing(Grant::policyId);
    private final ContextSnapshot snapshot;

    ContextAccessEngine(ContextSnapshot snapshot) { this.snapshot = snapshot; }

    Decision check(String type, String pageId, String endpointId, String fieldId) {
        ContextAccessContract.target(type, pageId, endpointId, fieldId);
        List<Stage> stages = new ArrayList<>();
        Stage page = stage("PAGE", pageId, null, null, snapshot.pages().containsKey(pageId));
        stages.add(page);
        if (!page.allowed()) return decision(type, pageId, endpointId, fieldId, "PAGE_NOT_GRANTED", stages);
        if ("PAGE".equals(type)) return decision(type, pageId, null, null, "ALLOWED", stages);

        boolean registered = snapshot.endpoints().containsKey(endpointId)
                && snapshot.bindings().contains(new ContextSnapshot.Binding(pageId, endpointId));
        Stage endpoint = stage("ENDPOINT", pageId, endpointId, null, registered);
        stages.add(endpoint);
        if (!endpoint.allowed()) return decision(type, pageId, endpointId, fieldId, "ENDPOINT_NOT_GRANTED", stages);
        if ("ENDPOINT".equals(type)) return decision(type, pageId, endpointId, null, "ALLOWED", stages);

        Field field = snapshot.fields().get(fieldId);
        Stage fieldStage = stage("FIELD", pageId, endpointId, fieldId,
                field != null && endpointId.equals(field.endpointId()));
        stages.add(fieldStage);
        return decision(type, pageId, endpointId, fieldId,
                fieldStage.allowed() ? "ALLOWED" : "FIELD_NOT_GRANTED", stages);
    }

    PageList pages(String after, int limit) {
        ContextAccessContract.page(after, limit);
        List<Page> visible = snapshot.pages().values().stream()
                .filter(p -> check("PAGE", p.id(), null, null).allowed())
                .sorted(Comparator.comparing(Page::id)).toList();
        Slice<Page> result = slice(visible, Page::id, after, limit);
        return new PageList(result.items(), result.nextCursor());
    }

    EndpointList endpoints(String pageId, String after, int limit) {
        ContextAccessContract.page(after, limit);
        requireVisible(check("PAGE", pageId, null, null));
        List<Endpoint> visible = snapshot.endpoints().values().stream()
                .filter(e -> check("ENDPOINT", pageId, e.id(), null).allowed())
                .sorted(Comparator.comparing(Endpoint::id)).toList();
        Slice<Endpoint> result = slice(visible, Endpoint::id, after, limit);
        return new EndpointList(pageId, result.items(), result.nextCursor());
    }

    FieldList fields(String pageId, String endpointId, String direction, String after, int limit) {
        ContextAccessContract.page(after, limit);
        ContextAccessContract.direction(direction);
        requireVisible(check("ENDPOINT", pageId, endpointId, null));
        List<Field> visible = snapshot.fields().values().stream()
                .filter(f -> endpointId.equals(f.endpointId()) && direction.equals(f.direction()))
                .filter(f -> check("FIELD", pageId, endpointId, f.id()).allowed())
                .sorted(Comparator.comparing(Field::id)).toList();
        Slice<Field> result = slice(visible, Field::id, after, limit);
        return new FieldList(pageId, endpointId, direction, result.items(), result.nextCursor());
    }

    private Stage stage(String kind, String page, String endpoint, String field, boolean registered) {
        List<Grant> grants = registered ? snapshot.rules().stream()
                .filter(r -> kind.equals(r.kind()) && page.equals(r.pageId())
                        && Objects.equals(endpoint, r.endpointId()) && Objects.equals(field, r.fieldId()))
                .map(ContextSnapshot.Rule::grant).distinct().sorted(GRANT_ORDER).toList() : List.of();
        return new Stage(kind, !grants.isEmpty(), grants.stream().limit(ContextAccessContract.MAX_GRANTS).toList(),
                grants.size() > ContextAccessContract.MAX_GRANTS);
    }

    private static Decision decision(String type, String page, String endpoint, String field, String reason, List<Stage> stages) {
        return new Decision(type, page, endpoint, field, "ALLOWED".equals(reason), reason, List.copyOf(stages));
    }

    private static void requireVisible(Decision decision) {
        if (!decision.allowed()) throw new ApiProblem(404, "NOT_FOUND");
    }

    private record Slice<T>(List<T> items, String nextCursor) {}

    private static <T> Slice<T> slice(List<T> sorted, Function<T, String> id, String after, int limit) {
        List<T> selected = sorted.stream().filter(item -> after == null || id.apply(item).compareTo(after) > 0)
                .limit(limit + 1L).toList();
        boolean more = selected.size() > limit;
        List<T> items = more ? List.copyOf(selected.subList(0, limit)) : selected;
        return new Slice<>(items, more ? id.apply(items.getLast()) : null);
    }
}
