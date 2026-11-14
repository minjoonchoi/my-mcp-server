package example.api;

import java.security.SecureRandom;
import java.util.Base64;
import com.fasterxml.jackson.databind.ObjectMapper;
import example.contract.AuthResponses.LoginResult;
import example.security.LoginProtocol;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class LoginService {
    private final LoginAccountRepository accounts;
    private final LoginTokenIssuer tokens;
    private final String issuer;
    private final ObjectMapper json;
    private final String dummyHash;

    public LoginService(LoginAccountRepository accounts, LoginTokenIssuer tokens, ObjectMapper json,
            @Value("${auth.issuer}") String issuer) {
        this.accounts = accounts; this.tokens = tokens; this.issuer = issuer; this.json = json;
        byte[] random = new byte[24]; new SecureRandom().nextBytes(random);
        dummyHash = PasswordHashes.hash(Base64.getUrlEncoder().withoutPadding().encodeToString(random));
    }

    public LoginResult login(LoginProtocol.Credentials credentials) {
        try {
            var account = accounts.find(credentials.username());
            boolean validHash = account != null && PasswordHashes.valid(account.passwordHash());
            boolean matches = PasswordHashes.matches(credentials.password(), validHash ? account.passwordHash() : dummyHash);
            // Unknown, disabled and invalid passwords all do exactly one fixed-cost verification.
            if (account == null || !account.enabled() || !validHash || !matches)
                throw new ApiProblem(401, "INVALID_CREDENTIALS");
            if (!issuer.equals(account.issuer()) || account.subject() == null || account.subject().isBlank()
                    || account.subject().length() > 512) throw new ApiProblem(503, "AUTH_UNAVAILABLE");
            var result = tokens.issue(account);
            // Also bound and validate the API's own public output, not only the relay's response.
            LoginProtocol.result(json.writeValueAsBytes(result));
            return result;
        } catch (ApiProblem safe) { throw safe; }
        catch (Exception unavailable) { throw new ApiProblem(503, "AUTH_UNAVAILABLE"); }
    }
}
