package example.mcp;

import example.security.ResourceSecurity;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import(ResourceSecurity.class)
public class McpApplication {
    public static void main(String[] args) {
        SpringApplication.run(McpApplication.class, args);
    }
}
