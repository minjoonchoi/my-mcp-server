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
            @Value("${demo.delegated:false}") boolean delegated,
            @Value("${auth.issuer:https://demo-issuer.invalid}") String userIssuer,
            @Value("${auth.public-key:${demo.public-key}}") Resource userPublicKey) throws Exception {
        JwtDecoder primary = pinned(json, readKey(publicKey), issuer, audience, delegated, false, userIssuer);
        if (!delegated) return primary;
        if (issuer.equals(userIssuer)) throw new IllegalArgumentException("JWT trust domains must be distinct");
        JwtDecoder direct = pinned(json, readKey(userPublicKey), userIssuer, audience, false, true, userIssuer);
        return token -> {
            // The unverified issuer only selects one fixed verifier; it supplies no authority or key URL.
            try {
                var raw = json.readTree(java.util.Base64.getUrlDecoder().decode(token.split("\\.")[1]));
                if (!raw.path("iss").isTextual()) throw new BadJwtException("Invalid token");
                String candidate = raw.path("iss").textValue();
                if (userIssuer.equals(candidate)) return direct.decode(token);
                if (issuer.equals(candidate)) return primary.decode(token);
            } catch (Exception ignored) { throw new BadJwtException("Invalid token"); }
            throw new BadJwtException("Invalid token");
        };
    }

    @Bean
    VerifiedCaller verifiedCaller(@Value("${auth.issuer:https://demo-issuer.invalid}") String userIssuer,
            @Value("${demo.issuer}") String issuer) {
        return new VerifiedCaller(userIssuer, issuer);
    }

    private static RSAPublicKey readKey(Resource resource) throws Exception {
        try (InputStream input = resource.getInputStream()) { return RsaKeyConverters.x509().convert(input); }
    }

    static JwtDecoder pinned(com.fasterxml.jackson.databind.ObjectMapper json, RSAPublicKey key,
            String issuer, String audience, boolean delegated, boolean direct, String userIssuer) {
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
                        && userIssuer.equals(jwt.getClaims().get("original_iss"))
                        && jwt.getClaims().get("scope") instanceof String
                        && jwt.getIssuedAt() != null && jwt.getExpiresAt() != null
                        && !jwt.getIssuedAt().isAfter(java.time.Instant.now())
                        && jwt.getExpiresAt().isAfter(jwt.getIssuedAt())
                        && Duration.between(jwt.getIssuedAt(), jwt.getExpiresAt()).getSeconds() <= 60;
            }
            if (direct) {
                valid = valid && !jwt.hasClaim("act") && !jwt.hasClaim("original_iss")
                        && jwt.getClaims().get("scope") instanceof String
                        && jwt.getIssuedAt() != null && jwt.getExpiresAt() != null
                        && !jwt.getIssuedAt().isAfter(java.time.Instant.now())
                        && jwt.getExpiresAt().isAfter(jwt.getIssuedAt())
                        && Duration.between(jwt.getIssuedAt(), jwt.getExpiresAt()).getSeconds() <= 900;
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
                        || (raw.has("nbf") && !raw.path("nbf").isNumber())
                        || ((direct || delegated) && !raw.path("iat").isNumber())) {
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
                    if (chatEnabled) auth.requestMatchers(HttpMethod.GET, "/chat.html", "/chat.js", "/chat.css").permitAll()
                            .requestMatchers(HttpMethod.POST, "/auth/login").permitAll();
                    if (delegated) {
                        auth.requestMatchers(HttpMethod.POST, "/api/auth/login").permitAll()
                                .requestMatchers(HttpMethod.GET, "/api/me", "/api/me/**", "/api/catalog/**").hasAuthority("SCOPE_access.read")
                                .requestMatchers(HttpMethod.PUT, "/api/manage/**").hasAuthority("SCOPE_access.manage")
                                .requestMatchers(HttpMethod.DELETE, "/api/manage/**").hasAuthority("SCOPE_access.manage");
                    } else auth.requestMatchers(endpoint).hasAuthority("SCOPE_" + scope);
                    auth.anyRequest().denyAll();
                })
                .headers(headers -> headers.contentSecurityPolicy(csp -> csp.policyDirectives(
                        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                        + "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")))
                .oauth2ResourceServer(oauth -> oauth
                        .bearerTokenResolver(request -> LoginProtocol.isLoginPath(request, delegated) ? null
                                : new org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver().resolve(request))
                        .jwt(jwt -> {})
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
                        boolean login = LoginProtocol.isLoginPath(request, delegated);
                        if (login) response.setHeader("Cache-Control", "no-store");
                        boolean chatAsset = "GET".equals(request.getMethod()) && java.util.Set.of("/chat.html", "/chat.js", "/chat.css").contains(request.getRequestURI());
                        boolean sameOriginMcp = chatEnabled && expectedOrigin.equals(origin)
                                && (chatAsset || login || ("/mcp".equals(request.getRequestURI()) && "POST".equals(request.getMethod())));
                        if ((origin != null && !sameOriginMcp)
                                || !"127.0.0.1".equals(host) || request.getServerPort() != request.getLocalPort()) {
                            response.setStatus(403);
                            if (login) {
                                response.setContentType("application/json");
                                response.getWriter().write("{\"code\":\"FORBIDDEN\"}");
                            }
                            return;
                        }
                        if (login) LoginRequestBoundary.filter(request, response, chain);
                        else chain.doFilter(request, response);
                    }
                }, BasicAuthenticationFilter.class);
        return http.build();
    }
}
