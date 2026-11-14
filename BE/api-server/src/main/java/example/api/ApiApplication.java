package example.api;

import example.security.ResourceSecurity;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import(ResourceSecurity.class)
public class ApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(ApiApplication.class, args);
    }

    @org.springframework.context.annotation.Bean
    org.springframework.beans.factory.InitializingBean rejectMixedProfiles(org.springframework.core.env.Environment environment) {
        return () -> {
            if (environment.matchesProfiles("local") && environment.matchesProfiles("fixture"))
                throw new IllegalStateException("local and fixture profiles must not be combined");
        };
    }
}
