CREATE TABLE users (
    id VARCHAR(64) PRIMARY KEY,
    issuer VARBINARY(1024) NOT NULL,
    subject VARBINARY(512) NOT NULL,
    name VARCHAR(200) NOT NULL,
    description VARCHAR(500) NOT NULL,
    CONSTRAINT uq_user_identity UNIQUE (issuer, subject)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE roles (
    id VARCHAR(64) PRIMARY KEY, name VARCHAR(200) NOT NULL, description VARCHAR(500) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE policies (
    id VARCHAR(64) PRIMARY KEY, name VARCHAR(200) NOT NULL, description VARCHAR(500) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE resources (
    id VARCHAR(64) PRIMARY KEY, name VARCHAR(200) NOT NULL, description VARCHAR(500) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE user_roles (
    user_id VARCHAR(64) NOT NULL, role_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (user_id, role_id), KEY ix_user_roles_role (role_id),
    FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (role_id) REFERENCES roles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE role_policies (
    role_id VARCHAR(64) NOT NULL, policy_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (role_id, policy_id), KEY ix_role_policies_policy (policy_id),
    FOREIGN KEY (role_id) REFERENCES roles(id), FOREIGN KEY (policy_id) REFERENCES policies(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
CREATE TABLE policy_resources (
    policy_id VARCHAR(64) NOT NULL, resource_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (policy_id, resource_id), KEY ix_policy_resources_resource (resource_id),
    FOREIGN KEY (policy_id) REFERENCES policies(id), FOREIGN KEY (resource_id) REFERENCES resources(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
