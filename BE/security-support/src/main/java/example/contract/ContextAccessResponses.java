package example.contract;

import java.util.List;

/** Bounded, identity-free wire responses for page-scoped authorization. */
public final class ContextAccessResponses {
    private ContextAccessResponses() {}

    public record Page(String id, String application, String path, String name) {}
    public record Endpoint(String id, String service, String method, String path, String name) {}
    public record Field(String id, String endpointId, String direction, String location, String path, String name) {}
    public record PageList(List<Page> items, String nextCursor) {}
    public record EndpointList(String pageId, List<Endpoint> items, String nextCursor) {}
    public record FieldList(String pageId, String endpointId, String direction, List<Field> items, String nextCursor) {}
    public record Grant(String roleId, String policyId) {}
    public record Stage(String stage, boolean allowed, List<Grant> grants, boolean grantsTruncated) {}
    public record Decision(String targetType, String pageId, String endpointId, String fieldId,
                           boolean allowed, String reason, List<Stage> stages) {}
}
