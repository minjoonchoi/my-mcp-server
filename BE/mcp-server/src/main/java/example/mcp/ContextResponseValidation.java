package example.mcp;

import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import example.contract.ContextAccessResponses.*;

/** Check bounded API responses before exposing metadata or grant evidence to clients. */
final class ContextResponseValidation {
    private static final List<String> STAGES = List.of("PAGE", "ENDPOINT", "FIELD");
    private static final Set<String> METHODS = Set.of("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS");
    private static final Comparator<Grant> GRANT_ORDER = Comparator.comparing(Grant::roleId).thenComparing(Grant::policyId);

    private ContextResponseValidation() {}

    static void pages(PageList response, String after, int limit) {
        require(response != null);
        page(response.items(), response.nextCursor(), after, limit, Page::id);
        for (Page item : response.items()) {
            identifier(item.application()); route(item.path()); name(item.name());
        }
    }

    static void endpoints(EndpointList response, String pageId, String after, int limit) {
        require(response != null && pageId.equals(response.pageId()));
        page(response.items(), response.nextCursor(), after, limit, Endpoint::id);
        for (Endpoint item : response.items()) {
            identifier(item.service());
            require(item.method() != null && METHODS.contains(item.method()));
            route(item.path()); name(item.name());
        }
    }

    static void fields(FieldList response, String pageId, String endpointId, String direction, String after, int limit) {
        require(response != null && pageId.equals(response.pageId()) && endpointId.equals(response.endpointId())
                && direction.equals(response.direction()));
        require(direction.equals("REQUEST") || direction.equals("RESPONSE"));
        page(response.items(), response.nextCursor(), after, limit, Field::id);
        for (Field item : response.items()) {
            require(endpointId.equals(item.endpointId()) && direction.equals(item.direction()));
            Set<String> locations = direction.equals("REQUEST") ? Set.of("PATH", "QUERY", "HEADER", "BODY") : Set.of("HEADER", "BODY");
            require(item.location() != null && locations.contains(item.location()));
            printablePath(item.path()); name(item.name());
        }
    }

    static void decision(Decision response, String type, String pageId, String endpointId, String fieldId) {
        require(response != null && type.equals(response.targetType()) && pageId.equals(response.pageId())
                && Objects.equals(endpointId, response.endpointId()) && Objects.equals(fieldId, response.fieldId()));
        int expectedStages = STAGES.indexOf(type) + 1;
        require(expectedStages > 0 && response.stages() != null && !response.stages().isEmpty()
                && response.stages().size() <= expectedStages);
        boolean denied = false;
        for (int index = 0; index < response.stages().size(); index++) {
            Stage stage = response.stages().get(index);
            require(stage != null && STAGES.get(index).equals(stage.stage()) && stage.grants() != null
                    && stage.grants().size() <= 5 && !denied);
            if (stage.allowed()) {
                require(!stage.grants().isEmpty() && (!stage.grantsTruncated() || stage.grants().size() == 5));
                Grant previous = null;
                for (Grant grant : stage.grants()) {
                    require(grant != null); identifier(grant.roleId()); identifier(grant.policyId());
                    require(previous == null || GRANT_ORDER.compare(previous, grant) < 0);
                    previous = grant;
                }
            } else {
                require(stage.grants().isEmpty() && !stage.grantsTruncated());
                denied = true;
            }
        }
        if (denied) {
            require(!response.allowed() && (response.stages().getLast().stage() + "_NOT_GRANTED").equals(response.reason()));
        } else {
            require(response.allowed() && response.stages().size() == expectedStages && "ALLOWED".equals(response.reason()));
        }
    }

    private static <T> void page(List<T> items, String cursor, String after, int limit, Function<T, String> getId) {
        require(limit >= 1 && limit <= 5 && items != null && items.size() <= limit);
        String previous = after;
        for (T item : items) {
            require(item != null);
            String id = getId.apply(item); identifier(id);
            require(previous == null || previous.compareTo(id) < 0);
            previous = id;
        }
        if (cursor != null) {
            identifier(cursor);
            require(items.size() == limit && cursor.equals(previous));
        }
    }

    private static void identifier(String value) {
        require(value != null && value.matches("[A-Za-z0-9_-]{1,64}"));
    }

    private static void name(String value) {
        require(value != null && !value.isBlank() && value.length() <= 80
                && value.codePoints().noneMatch(Character::isISOControl));
    }

    private static void route(String value) {
        printablePath(value);
        require(value.startsWith("/") && value.indexOf('?') < 0 && value.indexOf('#') < 0 && value.indexOf('\\') < 0);
    }

    private static void printablePath(String value) {
        require(value != null && !value.isBlank() && value.length() <= 200
                && value.chars().allMatch(character -> character >= 0x20 && character <= 0x7e));
    }

    private static void require(boolean condition) {
        if (!condition) throw new AccessApiClient.ApiFailure("UPSTREAM_INVALID_RESPONSE");
    }
}
