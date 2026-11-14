package example.api;

import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import example.contract.AuthResponses.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.Resource;
import org.springframework.security.converter.RsaKeyConverters;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.stereotype.Component;

/** API-only signer; separate audiences and scopes share the same bounded user session lifetime. */
@Component
public class LoginTokenIssuer {
    private final String issuer;
    private final long ttl;
    private final JwtEncoder encoder;

    public LoginTokenIssuer(@Value("${auth.issuer}") String issuer,
            @Value("${auth.token-ttl-seconds:900}") long ttl,
            @Value("${auth.private-key}") Resource privateKey,
            @Value("${auth.public-key}") Resource publicKey) {
        if (ttl < 60 || ttl > 900) throw new IllegalArgumentException("Login token lifetime must be 60..900 seconds");
        this.issuer = issuer;
        this.ttl = ttl;
        JwtEncoder available;
        try {
            RSAPrivateKey privateRsa;
            RSAPublicKey publicRsa;
            try (var input = privateKey.getInputStream()) { privateRsa = RsaKeyConverters.pkcs8().convert(input); }
            try (var input = publicKey.getInputStream()) { publicRsa = RsaKeyConverters.x509().convert(input); }
            if (!privateRsa.getModulus().equals(publicRsa.getModulus())) throw new IllegalArgumentException();
            var jwk = new RSAKey.Builder(publicRsa).privateKey(privateRsa).keyID("local-user").build();
            available = new NimbusJwtEncoder(new ImmutableJWKSet<>(new JWKSet(jwk)));
        } catch (Exception unavailable) { available = null; }
        // Missing login material does not disable pre-existing, independently verified bearer flows.
        encoder = available;
    }

    public LoginResult issue(LoginAccountRepository.Account account) {
        if (encoder == null || !issuer.equals(account.issuer())) throw new ApiProblem(503, "AUTH_UNAVAILABLE");
        Instant now = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        return new LoginResult("Bearer", ttl,
                token(account.subject(), "access-mcp", "mcp.connect authz.read" + (account.administrator() ? " authz.manage" : ""), now),
                token(account.subject(), "access-api", "access.read" + (account.administrator() ? " access.manage" : ""), now),
                new LoginUser(account.userId(), account.name()));
    }

    private String token(String subject, String audience, String scope, Instant now) {
        var claims = JwtClaimsSet.builder().issuer(issuer).subject(subject).audience(List.of(audience))
                .issuedAt(now).notBefore(now).expiresAt(now.plusSeconds(ttl)).claim("scope", scope).build();
        return encoder.encode(JwtEncoderParameters.from(JwsHeader.with(SignatureAlgorithm.RS256)
                .keyId("local-user").build(), claims)).getTokenValue();
    }
}
