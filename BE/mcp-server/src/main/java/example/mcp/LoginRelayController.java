package example.mcp;

import java.util.Map;
import example.security.LoginProtocol;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class LoginRelayController {
    private final LoginRelayClient client;
    public LoginRelayController(LoginRelayClient client) { this.client = client; }

    @PostMapping("/auth/login")
    public ResponseEntity<?> login(@RequestBody(required = false) byte[] body,
            @RequestParam Map<String, String> query) {
        try {
            if (!query.isEmpty()) throw new LoginProtocol.InvalidRequest();
            return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(client.login(LoginProtocol.credentials(body)));
        } catch (LoginProtocol.InvalidRequest invalid) { return error(400, "INVALID_ARGUMENT"); }
        catch (LoginRelayClient.Failure safe) { return error(safe.status, safe.getMessage()); }
    }

    private static ResponseEntity<?> error(int status, String code) {
        return ResponseEntity.status(status).cacheControl(CacheControl.noStore()).body(Map.of("code", code));
    }
}
