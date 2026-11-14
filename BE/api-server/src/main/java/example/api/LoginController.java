package example.api;

import java.util.Map;
import example.security.LoginProtocol;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class LoginController {
    private final LoginService service;
    public LoginController(LoginService service) { this.service = service; }

    @PostMapping("/api/auth/login")
    public ResponseEntity<?> login(@RequestBody(required = false) byte[] body,
            @RequestParam Map<String, String> query) {
        try {
            if (!query.isEmpty()) throw new LoginProtocol.InvalidRequest();
            return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(service.login(LoginProtocol.credentials(body)));
        } catch (LoginProtocol.InvalidRequest invalid) { return error(400, "INVALID_ARGUMENT"); }
        catch (ApiProblem safe) { return error(safe.status, safe.getMessage()); }
    }

    private static ResponseEntity<?> error(int status, String code) {
        return ResponseEntity.status(status).cacheControl(CacheControl.noStore()).body(Map.of("code", code));
    }
}
