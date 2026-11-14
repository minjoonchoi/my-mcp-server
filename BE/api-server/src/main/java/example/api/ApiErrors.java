package example.api;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.*;

@RestControllerAdvice
public class ApiErrors {
    @ExceptionHandler(ApiProblem.class)
    ResponseEntity<?> known(ApiProblem error) { return ResponseEntity.status(error.status).body(Map.of("code",error.getMessage())); }
    @ExceptionHandler(AccessDeniedException.class)
    ResponseEntity<?> denied() { return ResponseEntity.status(403).body(Map.of("code","FORBIDDEN")); }
    @ExceptionHandler(org.springframework.dao.DataAccessException.class)
    ResponseEntity<?> database() { return ResponseEntity.status(503).body(Map.of("code","UPSTREAM_UNAVAILABLE")); }
}
