package example.mcp;

import java.io.InputStream;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import com.nimbusds.jose.jwk.*;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.Resource;
import org.springframework.security.converter.RsaKeyConverters;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.stereotype.Component;

/** Local trust bridge, not OAuth token exchange. Key must be accessible only to the MCP backend. */
@Component
public class DelegatedTokenIssuer {
    private final JwtEncoder encoder;
    public DelegatedTokenIssuer(@Value("${delegation.private-key}") Resource privateKey,
            @Value("${delegation.public-key}") Resource publicKey) throws Exception {
        RSAPrivateKey privateRsa;
        RSAPublicKey publicRsa;
        try (InputStream input = privateKey.getInputStream()) { privateRsa = RsaKeyConverters.pkcs8().convert(input); }
        try (InputStream input = publicKey.getInputStream()) { publicRsa = RsaKeyConverters.x509().convert(input); }
        var jwk = new RSAKey.Builder(publicRsa).privateKey(privateRsa).keyID("local-delegation").build();
        encoder = new NimbusJwtEncoder(new ImmutableJWKSet<>(new JWKSet(jwk)));
    }

    public String issue(Jwt verifiedCaller, boolean management) {
        Instant now = Instant.now();
        Instant expires = verifiedCaller.getExpiresAt().isBefore(now.plusSeconds(60))
                ? verifiedCaller.getExpiresAt() : now.plusSeconds(60);
        if (!expires.isAfter(now)) throw new org.springframework.security.access.AccessDeniedException("FORBIDDEN");
        var claims = JwtClaimsSet.builder().issuer("https://demo-delegation.invalid")
                .subject(verifiedCaller.getSubject()).audience(List.of("access-api"))
                .issuedAt(now).notBefore(now).expiresAt(expires)
                .claim("scope", management ? "access.manage" : "access.read").claim("act", Map.of("sub", "access-mcp"))
                .claim("original_iss", verifiedCaller.getIssuer().toString()).build();
        return encoder.encode(JwtEncoderParameters.from(
                JwsHeader.with(SignatureAlgorithm.RS256).keyId("local-delegation").build(), claims)).getTokenValue();
    }
}
