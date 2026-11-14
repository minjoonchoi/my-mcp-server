package example.api;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import example.contract.ContextAccessResponses.*;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

@Repository
@Profile("!fixture")
@Transactional(readOnly = true)
class JdbcContextAccessRepository implements ContextAccessRepository {
    private final JdbcTemplate jdbc;

    JdbcContextAccessRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Override
    public ContextSnapshot snapshot(String originalIssuer, String subject) {
        List<String> actors = jdbc.queryForList("SELECT id FROM users WHERE issuer=? AND subject=?",
                String.class, originalIssuer, subject);
        if (actors.size() != 1) throw new ApiProblem(403, "FORBIDDEN");
        Map<String, Page> pages = new HashMap<>();
        Map<String, Endpoint> endpoints = new HashMap<>();
        Map<String, Field> fields = new HashMap<>();
        Set<ContextSnapshot.Binding> bindings = new HashSet<>();
        List<ContextSnapshot.Rule> rules = new ArrayList<>();
        // One joined query, scoped to this actor's current roles. Never load other users' grants.
        jdbc.query("""
                SELECT rp.role_id, c.policy_id, c.kind, c.page_id, c.endpoint_id, c.field_id,
                       p.application, p.path AS page_path, p.name AS page_name,
                       e.service, e.method, e.path AS endpoint_path, e.name AS endpoint_name,
                       f.direction, f.location, f.field_path, f.name AS field_name,
                       pe.endpoint_id AS bound_endpoint
                FROM user_roles ur
                JOIN role_policies rp ON rp.role_id=ur.role_id
                JOIN context_policy_rules c ON c.policy_id=rp.policy_id
                JOIN access_pages p ON p.id=c.page_id
                LEFT JOIN access_endpoints e ON e.id=c.endpoint_id
                LEFT JOIN access_fields f ON f.id=c.field_id AND f.endpoint_id=c.endpoint_id
                LEFT JOIN page_endpoints pe ON pe.page_id=c.page_id AND pe.endpoint_id=c.endpoint_id
                WHERE ur.user_id=? ORDER BY rp.role_id, c.policy_id
                """, rs -> {
                    String page = rs.getString("page_id"), endpoint = rs.getString("endpoint_id"), field = rs.getString("field_id");
                    pages.put(page, new Page(page, rs.getString("application"), rs.getString("page_path"), rs.getString("page_name")));
                    if (endpoint != null) endpoints.put(endpoint, new Endpoint(endpoint, rs.getString("service"),
                            rs.getString("method"), rs.getString("endpoint_path"), rs.getString("endpoint_name")));
                    if (field != null) fields.put(field, new Field(field, endpoint, rs.getString("direction"),
                            rs.getString("location"), rs.getString("field_path"), rs.getString("field_name")));
                    if (rs.getString("bound_endpoint") != null) bindings.add(new ContextSnapshot.Binding(page, endpoint));
                    rules.add(new ContextSnapshot.Rule(rs.getString("kind"), page, endpoint, field,
                            new Grant(rs.getString("role_id"), rs.getString("policy_id"))));
                }, actors.getFirst());
        return new ContextSnapshot(pages, endpoints, fields, bindings, rules);
    }
}
