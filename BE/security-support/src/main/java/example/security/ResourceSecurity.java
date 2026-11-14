package example.security;

import java.io.InputStream;
import java.time.Duration;
import java.security.interfaces.RSAPublicKey;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.converter.RsaKeyConverters;
import org.springframework.security.oauth2.core.*;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;

/** Shared validation only; each application declares its own audience and entry scope. */
@Configuration(proxyBeanMethods = false)
@EnableMethodSecurity
public class ResourceSecurity {
    @Bean
    JwtDecoder jwtDecoder(com.fasterxml.jackson.databind.ObjectMapper json,
            @Value("${demo.public-key}") Resource publicKey,
            @Value("${demo.issuer}") String issuer,
            @Value("${demo.audience}") String audience,
            @Value("${demo.delegated:false}") boolean delegated) throws Exception {
        RSAPublicKey key;
        try (InputStream input = publicKey.getInputStream()) {
            key = RsaKeyConverters.x509().convert(input);
        }
        var decoder = NimbusJwtDecoder.withPublicKey(key).build(); // RS256 only
        OAuth2TokenValidator<Jwt> requiredClaims = jwt -> {
            boolean valid = jwt.getExpiresAt() != null && jwt.getClaims().get("sub") instanceof String
                    && jwt.getSubject() != null
                    && !jwt.getSubject().isBlank() && jwt.getAudience() != null
                    && jwt.getAudience().contains(audience);
            if (delegated) {
                Object actor = jwt.getClaims().get("act");
                valid = valid && actor instanceof java.util.Map<?, ?> actorMap
                        && "access-mcp".equals(actorMap.get("sub"))
                        && "https://demo-issuer.invalid".equals(jwt.getClaims().get("original_iss"))
                        && jwt.getClaims().get("scope") instanceof String
                        && jwt.getIssuedAt() != null && jwt.getExpiresAt() != null
                        && !jwt.getIssuedAt().isAfter(java.time.Instant.now())
                        && jwt.getExpiresAt().isAfter(jwt.getIssuedAt())
                        && Duration.between(jwt.getIssuedAt(), jwt.getExpiresAt()).getSeconds() <= 60;
            }
            return valid ? OAuth2TokenValidatorResult.success()
                    : OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token"));
        };
        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
                new JwtTimestampValidator(Duration.ZERO), new JwtIssuerValidator(issuer), requiredClaims));
        return token -> {
            Jwt verified = decoder.decode(token);
            // Nimbus may coerce registered claims (e.g. numeric sub) before validators.
            // Inspect original types only after signature and semantic validation succeeded.
            try {
                var raw = json.readTree(java.util.Base64.getUrlDecoder().decode(token.split("\\.")[1]));
                if (!raw.path("sub").isTextual() || !raw.path("iss").isTextual()
                        || !raw.path("exp").isNumber()
                        || (raw.has("nbf") && !raw.path("nbf").isNumber())) {
                    throw new BadJwtException("Invalid token");
                }
                return verified;
            } catch (Exception invalid) {
                throw new BadJwtException("Invalid token");
            }
        };
    }

    @Bean
    SecurityFilterChain security(HttpSecurity http,
            @Value("${demo.endpoint}") String endpoint,
            @Value("${demo.entry-scope}") String scope,
            @Value("${demo.chat-enabled:false}") boolean chatEnabled,
            @Value("${demo.delegated:false}") boolean delegated) throws Exception {
        http.csrf(csrf -> csrf.disable()) // Only explicit Authorization headers, no cookie auth.
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .requestCache(cache -> cache.disable())
                .authorizeHttpRequests(auth -> {
                    if (chatEnabled) auth.requestMatchers(HttpMethod.GET, "/chat.html", "/chat.js", "/chat.css").permitAll();
                    if (delegated) {
                        auth.requestMatchers(HttpMethod.GET, "/api/me", "/api/me/**", "/api/catalog/**").hasAuthority("SCOPE_access.read")
                                .requestMatchers(HttpMethod.PUT, "/api/manage/**").hasAuthority("SCOPE_access.manage")
                                .requestMatchers(HttpMethod.DELETE, "/api/manage/**").hasAuthority("SCOPE_access.manage");
                    } else auth.requestMatchers(endpoint).hasAuthority("SCOPE_" + scope);
                    auth.anyRequest().denyAll();
                })
                .headers(headers -> headers.contentSecurityPolicy(csp -> csp.policyDirectives(
                        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                        + "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")))
                .oauth2ResourceServer(oauth -> oauth.jwt(jwt -> {})
                        .authenticationEntryPoint((req, res, error) -> {
                            res.setHeader("WWW-Authenticate", "Bearer");
                            res.setStatus(401);
                        }))
                .exceptionHandling(errors -> errors
                        .authenticationEntryPoint((req, res, error) -> {
                            res.setHeader("WWW-Authenticate", "Bearer");
                            res.setStatus(401);
                        })
                        .accessDeniedHandler((req, res, error) -> {
                            res.setStatus(403);
                            res.setContentType("application/json");
                            res.getWriter().write("{\"code\":\"FORBIDDEN\"}");
                        }))
                .addFilterBefore(new OncePerRequestFilter() {
                    @Override
                    protected void doFilterInternal(jakarta.servlet.http.HttpServletRequest request,
                            jakarta.servlet.http.HttpServletResponse response,
                            jakarta.servlet.FilterChain chain) throws java.io.IOException, jakarta.servlet.ServletException {
                        // Only the MCP chat app accepts an exact same-origin browser request.
                        String host = request.getServerName();
                        String origin = request.getHeader("Origin");
                        String expectedOrigin = "http://127.0.0.1:" + request.getLocalPort();
                        boolean chatAsset = "GET".equals(request.getMethod()) && java.util.Set.of("/chat.html", "/chat.js", "/chat.css").contains(request.getRequestURI());
                        boolean sameOriginMcp = chatEnabled && expectedOrigin.equals(origin)
                                && (chatAsset || ("/mcp".equals(request.getRequestURI()) && "POST".equals(request.getMethod())));
                        if ((origin != null && !sameOriginMcp)
                                || !"127.0.0.1".equals(host) || request.getServerPort() != request.getLocalPort()) {
                            response.setStatus(403);
                            return;
                        }
                        chain.doFilter(request, response);
                    }
                }, BasicAuthenticationFilter.class);
        return http.build();
    }
}
