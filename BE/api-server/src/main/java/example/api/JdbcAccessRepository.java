package example.api;

import java.util.*;
import example.contract.AccessResponses.*;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

@Repository
@Profile("!fixture")
@Transactional(readOnly = true)
public class JdbcAccessRepository implements AccessRepository {
    private final JdbcTemplate jdbc;
    public JdbcAccessRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Override
    @Transactional(readOnly = true)
    public MyAccess access(String issuer, String subject) {
        var users = jdbc.query("SELECT id, name, description FROM users WHERE issuer = ? AND subject = ?",
                (rs, row) -> new User(rs.getString("id"), rs.getString("name"), rs.getString("description"), List.of()), issuer, subject);
        if (users.size() != 1) throw new AccessDeniedException("FORBIDDEN");
        User identity = users.getFirst();
        var ownRoles = jdbc.query("""
                SELECT r.id, r.name, r.description FROM roles r
                JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id = ? ORDER BY r.id
                """, (rs, row) -> new Role(rs.getString("id"), rs.getString("name"), rs.getString("description"), List.of()), identity.id());
        // Start from this user's assigned roles. Never compute provenance from all holders of a policy.
        var paths = jdbc.queryForList("""
                SELECT r.id AS role_id, p.id AS policy_id, p.name AS policy_name, p.description AS policy_description,
                       s.id AS resource_id, s.name AS resource_name, s.description AS resource_description
                FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                JOIN role_policies rp ON rp.role_id = r.id JOIN policies p ON p.id = rp.policy_id
                LEFT JOIN policy_resources pr ON pr.policy_id = p.id LEFT JOIN resources s ON s.id = pr.resource_id
                WHERE ur.user_id = ? ORDER BY r.id, p.id, s.id
                """, identity.id());
        Map<String, SortedSet<String>> rolePolicies = new TreeMap<>(), policyRoles = new TreeMap<>(), policyResources = new TreeMap<>();
        Map<String, Policy> policyInfo = new TreeMap<>();
        Map<String, Resource> resourceInfo = new TreeMap<>();
        Map<String, SortedSet<Grant>> grants = new TreeMap<>();
        var grantOrder = Comparator.comparing(Grant::roleId).thenComparing(Grant::policyId);
        for (var path : paths) {
            String role = (String) path.get("role_id"), policy = (String) path.get("policy_id"), resource = (String) path.get("resource_id");
            rolePolicies.computeIfAbsent(role, key -> new TreeSet<>()).add(policy);
            policyRoles.computeIfAbsent(policy, key -> new TreeSet<>()).add(role);
            policyInfo.putIfAbsent(policy, new Policy(policy, (String) path.get("policy_name"), (String) path.get("policy_description"), List.of(), List.of()));
            if (resource != null) {
                policyResources.computeIfAbsent(policy, key -> new TreeSet<>()).add(resource);
                resourceInfo.putIfAbsent(resource, new Resource(resource, (String) path.get("resource_name"), (String) path.get("resource_description")));
                grants.computeIfAbsent(resource, key -> new TreeSet<>(grantOrder)).add(new Grant(role, policy));
            }
        }
        var roles = ownRoles.stream().sorted(Comparator.comparing(Role::id)).map(r -> new Role(r.id(), r.name(), r.description(),
                List.copyOf(rolePolicies.getOrDefault(r.id(), new TreeSet<>())))).toList();
        var policies = policyInfo.values().stream().map(p -> new Policy(p.id(), p.name(), p.description(),
                List.copyOf(policyResources.getOrDefault(p.id(), new TreeSet<>())), List.copyOf(policyRoles.get(p.id())))).toList();
        var resources = resourceInfo.values().stream().map(r -> new GrantedResource(r.id(), r.name(), r.description(), List.copyOf(grants.get(r.id())))).toList();
        return new MyAccess(new User(identity.id(), identity.name(), identity.description(), roles.stream().map(Role::id).toList()), roles, policies, resources);
    }
    private String actorId(String issuer, String subject) {
        var ids = jdbc.queryForList("SELECT id FROM users WHERE issuer = ? AND subject = ?", String.class, issuer, subject);
        if (ids.size() != 1) throw new ApiProblem(403, "FORBIDDEN");
        return ids.getFirst();
    }
    private boolean admin(String userId) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=? AND r.id='SYSTEM_ADMIN' AND r.protected=TRUE", Integer.class, userId) == 1;
    }
    @Override
    public CatalogList catalog(String issuer, String subject, String entity) {
        ManagementContract.validEntity(entity);
        String actor = actorId(issuer, subject);
        boolean full = admin(actor);
        MyAccess own = access(issuer, subject);
        Set<String> visible = switch (entity) {
            case "users" -> Set.of(own.user().id());
            case "roles" -> new HashSet<>(own.user().roleIds());
            case "policies" -> own.policies().stream().map(Policy::id).collect(java.util.stream.Collectors.toSet());
            default -> own.resources().stream().map(GrantedResource::id).collect(java.util.stream.Collectors.toSet());
        };
        // entity is a validated closed enum; all data values remain bound parameters.
        var items = jdbc.query("SELECT id,name,description" + (entity.equals("users") ? "" : ",protected")
                + (entity.equals("roles") ? ",requestable" : "") + " FROM " + entity + " ORDER BY id", (rs, row) -> {
            Map<String,Object> item = new LinkedHashMap<>();
            String id = rs.getString("id");
            item.put("id",id); item.put("name",rs.getString("name")); item.put("description",rs.getString("description"));
            if (!entity.equals("users")) item.put("protected",rs.getBoolean("protected"));
            switch (entity) {
                case "users" -> item.put("roleIds", jdbc.queryForList("SELECT role_id FROM user_roles WHERE user_id=? ORDER BY role_id",String.class,id));
                case "roles" -> {
                    item.put("requestable",rs.getBoolean("requestable"));
                    item.put("policyIds", jdbc.queryForList("SELECT policy_id FROM role_policies WHERE role_id=? ORDER BY policy_id",String.class,id));
                }
                case "policies" -> item.put("resourceIds", jdbc.queryForList("SELECT resource_id FROM policy_resources WHERE policy_id=? ORDER BY resource_id",String.class,id));
                default -> { }
            }
            return item;
        }).stream().filter(item -> full || visible.contains(item.get("id")))
                .sorted(Comparator.comparing(item -> (String)item.get("id"))).toList();
        return new CatalogList(entity,items);
    }
    @Override
    public RequestableRoles requestable(String issuer, String subject) {
        String actor = actorId(issuer,subject);
        return new RequestableRoles(jdbc.query("""
                SELECT r.id,r.name,r.description FROM roles r WHERE r.requestable=TRUE AND r.protected=FALSE
                AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id=? AND ur.role_id=r.id) ORDER BY r.id
                """, (rs,row) -> new EntitySummary(rs.getString("id"),rs.getString("name"),rs.getString("description")),actor));
    }
    @Override
    @Transactional
    public MutationResult change(String issuer, String subject, String operation, Map<String,String> targets) {
        // One protected anchor serializes this bounded management demo across API instances.
        // Every validation, impact snapshot, link update and audit insert shares this transaction.
        var anchor = jdbc.queryForList("SELECT id FROM roles WHERE id='SYSTEM_ADMIN' AND protected=TRUE FOR UPDATE",String.class);
        String actor = actorId(issuer,subject);
        if (anchor.isEmpty() || !admin(actor)) throw new ApiProblem(403,"FORBIDDEN");
        var relation = ManagementContract.relation(operation);
        ManagementContract.validTargets(relation,targets);
        String left = targets.get(relation.leftKey()), right = targets.get(relation.rightKey());
        var leftRows = target(relation.leftTable(),left);
        var rightRows = target(relation.rightTable(),right);
        if (leftRows.stream().anyMatch(Boolean::booleanValue) || rightRows.stream().anyMatch(Boolean::booleanValue))
            throw new ApiProblem(403,"FORBIDDEN");
        if (leftRows.isEmpty() || rightRows.isEmpty()) throw new ApiProblem(404,"NOT_FOUND");
        if (relation.table().equals("policy_resources") && jdbc.queryForObject(
                "SELECT COUNT(*) FROM context_policy_rules WHERE policy_id=?", Integer.class, left) > 0)
            throw new ApiProblem(400, "INVALID_ARGUMENT");
        List<String> affected = switch (relation.table()) {
            case "user_roles" -> List.of(left);
            case "role_policies" -> jdbc.queryForList("SELECT user_id FROM user_roles WHERE role_id=? ORDER BY user_id",String.class,left);
            default -> jdbc.queryForList("SELECT DISTINCT ur.user_id FROM user_roles ur JOIN role_policies rp ON ur.role_id=rp.role_id WHERE rp.policy_id=? ORDER BY ur.user_id",String.class,left);
        };
        String where = relation.leftColumn()+"=? AND "+relation.rightColumn()+"=?";
        boolean before = jdbc.queryForObject("SELECT COUNT(*) FROM "+relation.table()+" WHERE "+where,Integer.class,left,right) > 0;
        boolean changed = before != relation.add();
        if (changed) {
            if (relation.add()) jdbc.update("INSERT INTO "+relation.table()+" ("+relation.leftColumn()+","+relation.rightColumn()+") VALUES (?,?)",left,right);
            else jdbc.update("DELETE FROM "+relation.table()+" WHERE "+where,left,right);
        }
        jdbc.update("INSERT INTO access_audit (actor_issuer,actor_subject,actor_user_id,operation,left_id,right_id,success,changed) VALUES (?,?,?,?,?,?,TRUE,?)",
                issuer,subject,actor,operation,left,right,changed);
        return new MutationResult(operation,changed,Map.copyOf(targets),relation.add(),new Impact(affected.stream().distinct().sorted().toList()));
    }
    private List<Boolean> target(String table,String id) {
        return jdbc.queryForList("SELECT "+(table.equals("users") ? "FALSE" : "protected")+" FROM "+table+" WHERE id=? FOR UPDATE",Boolean.class,id);
    }

}
