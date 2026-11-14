package example.api;

import java.util.List;
import java.util.Map;
import example.contract.AccessResponses.*;

public interface AccessRepository {
    MyAccess access(String originalIssuer, String subject);
    CatalogList catalog(String issuer, String subject, String entity);
    RequestableRoles requestable(String issuer, String subject);
    MutationResult change(String issuer, String subject, String operation, Map<String, String> targets);

    default CatalogDetail detail(String issuer, String subject, String entity, String id) {
        ManagementContract.validId(entity, id);
        return catalog(issuer, subject, entity).items().stream().filter(item -> id.equals(item.get("id")))
                .findFirst().map(item -> new CatalogDetail(entity, item)).orElseThrow(() -> new ApiProblem(404, "NOT_FOUND"));
    }

    default ResourceAccess resourceAccess(String issuer, String subject, String resourceId) {
        return access(issuer, subject).resources().stream().filter(r -> resourceId.equals(r.id()))
                .findFirst().map(r -> new ResourceAccess(resourceId, true,
                        new Resource(r.id(), r.name(), r.description()), r.grants()))
                .orElseGet(() -> new ResourceAccess(resourceId, false, null, List.of()));
    }
}
