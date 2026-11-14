package example.api;

import java.util.Set;

/** Shared API/engine constraints. No caller identity is accepted in this contract. */
final class ContextAccessContract {
    static final int MAX_PAGE_SIZE = 5;
    static final int MAX_GRANTS = 5;
    static final Set<String> TARGETS = Set.of("PAGE", "ENDPOINT", "FIELD");
    static final Set<String> METHODS = Set.of("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS");

    private ContextAccessContract() {}

    static boolean isId(String value) {
        return value != null && value.matches("[A-Za-z0-9_-]{1,64}");
    }

    static void id(String value) {
        if (!isId(value)) throw invalid();
    }

    static void page(String after, int limit) {
        if (after != null) id(after);
        if (limit < 1 || limit > MAX_PAGE_SIZE) throw invalid();
    }

    static void direction(String value) {
        if (!"REQUEST".equals(value) && !"RESPONSE".equals(value)) throw invalid();
    }

    static void target(String type, String pageId, String endpointId, String fieldId) {
        if (!TARGETS.contains(type == null ? "" : type)) throw invalid();
        id(pageId);
        if ("PAGE".equals(type)) {
            if (endpointId != null || fieldId != null) throw invalid();
        } else {
            id(endpointId);
            if ("FIELD".equals(type)) id(fieldId);
            else if (fieldId != null) throw invalid();
        }
    }

    static boolean path(String value) {
        return printable(value, 200) && value.startsWith("/")
                && value.indexOf('?') < 0 && value.indexOf('#') < 0 && value.indexOf('\\') < 0;
    }

    static boolean printable(String value, int length) {
        return value != null && !value.isBlank() && value.length() <= length
                && value.chars().allMatch(c -> c >= 32 && c <= 126);
    }

    static boolean name(String value) {
        return value != null && !value.isBlank() && value.length() <= 80
                && value.codePoints().noneMatch(Character::isISOControl);
    }

    static boolean location(String direction, String location) {
        return "REQUEST".equals(direction)
                ? Set.of("PATH", "QUERY", "HEADER", "BODY").contains(location == null ? "" : location)
                : "RESPONSE".equals(direction) && Set.of("HEADER", "BODY").contains(location == null ? "" : location);
    }

    static void configuration(boolean valid) {
        if (!valid) throw new ApiProblem(503, "UPSTREAM_INVALID_RESPONSE");
    }

    static ApiProblem invalid() { return new ApiProblem(400, "INVALID_ARGUMENT"); }
}
