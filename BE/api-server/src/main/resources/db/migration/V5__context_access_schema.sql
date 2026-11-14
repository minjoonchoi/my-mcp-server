-- Typed definitions and page-scoped policies are separate from legacy R### resources.
-- Definition registration is an operator migration; role/policy assignments use audited APIs.
CREATE TABLE access_pages (
    id VARCHAR(64) PRIMARY KEY,
    application VARCHAR(64) NOT NULL,
    path VARCHAR(200) NOT NULL,
    name VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_access_page_path (application, path),
    CONSTRAINT ck_access_page_id CHECK (CHAR_LENGTH(id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_page_application CHECK (CHAR_LENGTH(application) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(application, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_page_path CHECK (LEFT(path,1)='/' AND CHAR_LENGTH(path) BETWEEN 1 AND 200 AND NOT REGEXP_LIKE(path, '[^ -~]', 'c')
        AND LOCATE('?',path)=0 AND LOCATE('#',path)=0 AND LOCATE(CHAR(92),path)=0),
    CONSTRAINT ck_access_page_name CHECK (NOT REGEXP_LIKE(name, '^[[:space:]]*$')
        AND NOT REGEXP_LIKE(name, '[[:cntrl:]]') AND OCTET_LENGTH(CONVERT(name USING utf16)) <= 160)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE access_endpoints (
    id VARCHAR(64) PRIMARY KEY,
    service VARCHAR(64) NOT NULL,
    method VARCHAR(7) NOT NULL,
    path VARCHAR(200) NOT NULL,
    name VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_access_endpoint_route (service, method, path),
    CONSTRAINT ck_access_endpoint_id CHECK (CHAR_LENGTH(id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_endpoint_service CHECK (CHAR_LENGTH(service) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(service, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_endpoint_method CHECK (CAST(method AS BINARY) IN ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')),
    CONSTRAINT ck_access_endpoint_path CHECK (LEFT(path,1)='/' AND CHAR_LENGTH(path) BETWEEN 1 AND 200 AND NOT REGEXP_LIKE(path, '[^ -~]', 'c')
        AND LOCATE('?',path)=0 AND LOCATE('#',path)=0 AND LOCATE(CHAR(92),path)=0),
    CONSTRAINT ck_access_endpoint_name CHECK (NOT REGEXP_LIKE(name, '^[[:space:]]*$')
        AND NOT REGEXP_LIKE(name, '[[:cntrl:]]') AND OCTET_LENGTH(CONVERT(name USING utf16)) <= 160)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE access_fields (
    id VARCHAR(64) PRIMARY KEY,
    endpoint_id VARCHAR(64) NOT NULL,
    direction VARCHAR(8) NOT NULL,
    location VARCHAR(6) NOT NULL,
    field_path VARCHAR(200) NOT NULL,
    name VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_access_field_path (endpoint_id, direction, location, field_path),
    UNIQUE KEY uq_access_field_endpoint_id (endpoint_id, id),
    FOREIGN KEY (endpoint_id) REFERENCES access_endpoints(id),
    CONSTRAINT ck_access_field_id CHECK (CHAR_LENGTH(id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_field_endpoint CHECK (CHAR_LENGTH(endpoint_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(endpoint_id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_access_field_location CHECK (
        (CAST(direction AS BINARY)='REQUEST' AND CAST(location AS BINARY) IN ('PATH','QUERY','HEADER','BODY'))
        OR (CAST(direction AS BINARY)='RESPONSE' AND CAST(location AS BINARY) IN ('HEADER','BODY'))),
    CONSTRAINT ck_access_field_path CHECK (TRIM(field_path) <> '' AND CHAR_LENGTH(field_path) BETWEEN 1 AND 200
        AND NOT REGEXP_LIKE(field_path, '[^ -~]', 'c')),
    CONSTRAINT ck_access_field_name CHECK (NOT REGEXP_LIKE(name, '^[[:space:]]*$')
        AND NOT REGEXP_LIKE(name, '[[:cntrl:]]') AND OCTET_LENGTH(CONVERT(name USING utf16)) <= 160)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE page_endpoints (
    page_id VARCHAR(64) NOT NULL,
    endpoint_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (page_id, endpoint_id),
    FOREIGN KEY (page_id) REFERENCES access_pages(id),
    FOREIGN KEY (endpoint_id) REFERENCES access_endpoints(id),
    CONSTRAINT ck_page_endpoint_page CHECK (CHAR_LENGTH(page_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(page_id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_page_endpoint_endpoint CHECK (CHAR_LENGTH(endpoint_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(endpoint_id, '[^A-Za-z0-9_-]', 'c'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE context_policy_rules (
    policy_id VARCHAR(64) PRIMARY KEY,
    kind VARCHAR(8) NOT NULL,
    page_id VARCHAR(64) NOT NULL,
    endpoint_id VARCHAR(64),
    field_id VARCHAR(64),
    KEY ix_context_rule_page (page_id, endpoint_id, field_id),
    FOREIGN KEY (policy_id) REFERENCES policies(id),
    FOREIGN KEY (page_id) REFERENCES access_pages(id),
    FOREIGN KEY (page_id, endpoint_id) REFERENCES page_endpoints(page_id, endpoint_id),
    FOREIGN KEY (endpoint_id, field_id) REFERENCES access_fields(endpoint_id, id),
    CONSTRAINT ck_context_rule_id CHECK (CHAR_LENGTH(policy_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(policy_id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_context_rule_page CHECK (CHAR_LENGTH(page_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(page_id, '[^A-Za-z0-9_-]', 'c')),
    CONSTRAINT ck_context_rule_endpoint CHECK (endpoint_id IS NULL OR
        (CHAR_LENGTH(endpoint_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(endpoint_id, '[^A-Za-z0-9_-]', 'c'))),
    CONSTRAINT ck_context_rule_field CHECK (field_id IS NULL OR
        (CHAR_LENGTH(field_id) BETWEEN 1 AND 64 AND NOT REGEXP_LIKE(field_id, '[^A-Za-z0-9_-]', 'c'))),
    CONSTRAINT ck_context_rule_shape CHECK (
        (CAST(kind AS BINARY)='PAGE' AND endpoint_id IS NULL AND field_id IS NULL)
        OR (CAST(kind AS BINARY)='ENDPOINT' AND endpoint_id IS NOT NULL AND field_id IS NULL)
        OR (CAST(kind AS BINARY)='FIELD' AND endpoint_id IS NOT NULL AND field_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
