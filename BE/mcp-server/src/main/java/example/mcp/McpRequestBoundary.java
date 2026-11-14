package example.mcp;

import java.io.*;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.springframework.boot.autoconfigure.security.SecurityProperties;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

/** Applied after Spring Security: bounded input and a safe SDK protocol error boundary. */
@Component
@Order(SecurityProperties.DEFAULT_FILTER_ORDER + 1)
public class McpRequestBoundary extends OncePerRequestFilter {
    private static final int MAX_BYTES = 16 * 1024;
    private final ObjectMapper json;

    public McpRequestBoundary(ObjectMapper json) { this.json = json; }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        // Security already restricts accessible endpoints. Raw URI spelling must not
        // bypass this boundary when Spring MVC decodes ordinary percent-encoded letters.
        return !"POST".equals(request.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
            FilterChain chain) throws IOException, ServletException {
        if (request.getContentLengthLong() > MAX_BYTES) {
            safeError(response, 413);
            return;
        }
        // Enforce the actual stream size too: Content-Length may be absent (chunked).
        byte[] body = request.getInputStream().readNBytes(MAX_BYTES + 1);
        if (body.length > MAX_BYTES) {
            safeError(response, 413);
            return;
        }
        var boundedRequest = new HttpServletRequestWrapper(request) {
            @Override
            public ServletInputStream getInputStream() {
                var input = new ByteArrayInputStream(body);
                return new ServletInputStream() {
                    public int read() { return input.read(); }
                    public boolean isFinished() { return input.available() == 0; }
                    public boolean isReady() { return true; }
                    public void setReadListener(ReadListener listener) {
                        throw new UnsupportedOperationException("Synchronous transport only");
                    }
                };
            }
            @Override
            public BufferedReader getReader() {
                return new BufferedReader(new InputStreamReader(getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
            }
        };
        var buffered = new ContentCachingResponseWrapper(response);
        try {
            chain.doFilter(boundedRequest, buffered);
            if (buffered.getStatus() >= 400) {
                safeError(buffered, buffered.getStatus());
            } else if (buffered.getContentSize() > 0) {
                JsonNode payload = json.readTree(buffered.getContentAsByteArray());
                if (payload != null && payload.has("error")) {
                    // Preserve a valid JSON-RPC response id but discard internal error data.
                    var clean = json.createObjectNode().put("jsonrpc", "2.0");
                    clean.set("id", payload.path("id"));
                    clean.putObject("error").put("code", -32603).put("message", "MCP_REQUEST_FAILED");
                    buffered.resetBuffer();
                    buffered.getOutputStream().write(json.writeValueAsBytes(clean));
                }
            }
        } catch (Exception ignored) {
            safeError(buffered, 500);
        }
        buffered.copyBodyToResponse();
    }

    private static void safeError(HttpServletResponse response, int status) throws IOException {
        response.resetBuffer();
        response.setStatus(status);
        response.setContentType("application/json");
        response.getOutputStream().write("{\"code\":\"MCP_REQUEST_REJECTED\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
}
