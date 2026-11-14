-- Local examples match ContextFixtures. Existing legacy resources and grants are preserved.
INSERT INTO access_pages (id,application,path,name) VALUES
('page-customers','backoffice','/admin/customers','Customer support'),
('page-audit','backoffice','/admin/audit','Customer audit');
INSERT INTO access_endpoints (id,service,method,path,name) VALUES
('endpoint-customer-get','customer-api','GET','/customers/{id}','Get customer');
INSERT INTO access_fields (id,endpoint_id,direction,location,field_path,name) VALUES
('field-customer-id-request','endpoint-customer-get','REQUEST','PATH','id','Customer ID input'),
('field-customer-name-response','endpoint-customer-get','RESPONSE','BODY','name','Customer name output'),
('field-customer-email-response','endpoint-customer-get','RESPONSE','BODY','email','Customer email output');
INSERT INTO page_endpoints (page_id,endpoint_id) VALUES
('page-customers','endpoint-customer-get'),('page-audit','endpoint-customer-get');
INSERT INTO policies (id,name,description,protected) VALUES
('ctx-page-customers','ctx-page-customers','Page-scoped authorization rule',FALSE),
('ctx-page-audit','ctx-page-audit','Page-scoped authorization rule',FALSE),
('ctx-endpoint-customers','ctx-endpoint-customers','Page-scoped authorization rule',FALSE),
('ctx-endpoint-audit','ctx-endpoint-audit','Page-scoped authorization rule',FALSE),
('ctx-field-customers-id','ctx-field-customers-id','Page-scoped authorization rule',FALSE),
('ctx-field-customers-name','ctx-field-customers-name','Page-scoped authorization rule',FALSE),
('ctx-field-audit-id','ctx-field-audit-id','Page-scoped authorization rule',FALSE),
('ctx-field-audit-name','ctx-field-audit-name','Page-scoped authorization rule',FALSE),
('ctx-field-audit-email','ctx-field-audit-email','Page-scoped authorization rule',FALSE);
INSERT INTO context_policy_rules (policy_id,kind,page_id,endpoint_id,field_id) VALUES
('ctx-page-customers','PAGE','page-customers',NULL,NULL),
('ctx-page-audit','PAGE','page-audit',NULL,NULL),
('ctx-endpoint-customers','ENDPOINT','page-customers','endpoint-customer-get',NULL),
('ctx-endpoint-audit','ENDPOINT','page-audit','endpoint-customer-get',NULL),
('ctx-field-customers-id','FIELD','page-customers','endpoint-customer-get','field-customer-id-request'),
('ctx-field-customers-name','FIELD','page-customers','endpoint-customer-get','field-customer-name-response'),
('ctx-field-audit-id','FIELD','page-audit','endpoint-customer-get','field-customer-id-request'),
('ctx-field-audit-name','FIELD','page-audit','endpoint-customer-get','field-customer-name-response'),
('ctx-field-audit-email','FIELD','page-audit','endpoint-customer-get','field-customer-email-response');
INSERT INTO role_policies (role_id,policy_id) VALUES
('role-analyst','ctx-page-customers'),('role-analyst','ctx-endpoint-customers'),
('role-analyst','ctx-field-customers-id'),('role-analyst','ctx-field-customers-name'),
('role-analyst','ctx-field-audit-email'),
('role-editor','ctx-page-audit'),('role-editor','ctx-endpoint-audit'),
('role-editor','ctx-field-audit-id'),('role-editor','ctx-field-audit-name');
