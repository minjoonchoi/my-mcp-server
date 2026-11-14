package example.api;

import java.util.Map;
import java.util.Set;

final class ManagementContract {
    private ManagementContract() {}
    record Relation(String table, String leftTable, String rightTable, String leftColumn, String rightColumn,
                    String leftKey, String rightKey, boolean add) {}
    static Relation relation(String operation) {
        return switch (operation) {
            case "assign_role_to_user", "remove_role_from_user" -> new Relation("user_roles", "users", "roles", "user_id", "role_id", "userId", "roleId", operation.startsWith("assign"));
            case "attach_policy_to_role", "detach_policy_from_role" -> new Relation("role_policies", "roles", "policies", "role_id", "policy_id", "roleId", "policyId", operation.startsWith("attach"));
            case "attach_resource_to_policy", "detach_resource_from_policy" -> new Relation("policy_resources", "policies", "resources", "policy_id", "resource_id", "policyId", "resourceId", operation.startsWith("attach"));
            default -> throw new ApiProblem(400, "INVALID_ARGUMENT");
        };
    }
    static void validEntity(String entity) {
        if (!Set.of("users", "roles", "policies", "resources").contains(entity)) throw new ApiProblem(400, "INVALID_ARGUMENT");
    }
    static void validId(String entity, String id) {
        validEntity(entity);
        if (id == null || !id.matches(entity.equals("resources") ? "R[0-9]{3}" : "[A-Za-z0-9_-]{1,64}")) throw new ApiProblem(400, "INVALID_ARGUMENT");
    }
    static void validTargets(Relation relation, Map<String, String> targets) {
        if (!targets.keySet().equals(Set.of(relation.leftKey(), relation.rightKey()))) throw new ApiProblem(400, "INVALID_ARGUMENT");
        validId(relation.leftTable(), targets.get(relation.leftKey()));
        validId(relation.rightTable(), targets.get(relation.rightKey()));
    }
}
