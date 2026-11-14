package example.api;

import java.nio.charset.StandardCharsets;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

@Repository
@Profile("!fixture")
@Transactional(readOnly = true)
public class JdbcLoginAccountRepository implements LoginAccountRepository {
    private final JdbcTemplate jdbc;
    public JdbcLoginAccountRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Override public Account find(String username) {
        var accounts = jdbc.query("""
                SELECT u.id,u.issuer,u.subject,u.name,a.password_hash,a.enabled,
                  EXISTS(SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                         WHERE ur.user_id=u.id AND r.id='SYSTEM_ADMIN' AND r.protected=TRUE) AS administrator
                FROM login_accounts a JOIN users u ON u.id=a.user_id WHERE a.username=?
                """, (rs, row) -> new Account(rs.getString("id"), rs.getString("issuer"), rs.getString("subject"),
                rs.getString("name"), rs.getString("password_hash"), rs.getBoolean("enabled"), rs.getBoolean("administrator")),
                username.getBytes(StandardCharsets.US_ASCII));
        return accounts.isEmpty() ? null : accounts.getFirst();
    }
}
