const PROTOCOL = '2025-06-18';
const MAX_RESPONSE = 128 * 1024;
const MAX_LOGIN_RESPONSE = 16 * 1024;
const TIMEOUT = 12000;
const CATALOGS = ['users', 'roles', 'policies', 'resources'];
const ID_PATTERN = '^[A-Za-z0-9_-]{1,64}$';
export const MANAGEMENT = Object.freeze({
  assign_role_to_user: { keys: ['userId', 'roleId'], label: '사용자 → 역할 추가', present: true },
  remove_role_from_user: { keys: ['userId', 'roleId'], label: '사용자 → 역할 해제', present: false },
  attach_policy_to_role: { keys: ['roleId', 'policyId'], label: '역할 → 정책 추가', present: true },
  detach_policy_from_role: { keys: ['roleId', 'policyId'], label: '역할 → 정책 해제', present: false },
  attach_resource_to_policy: { keys: ['policyId', 'resourceId'], label: '정책 → 리소스 추가', present: true },
  detach_resource_from_policy: { keys: ['policyId', 'resourceId'], label: '정책 → 리소스 해제', present: false },
});
const TOOL_KEYS = {
  get_my_access: [], check_my_resource_access: ['resourceId'], explain_authorization: ['concept'],
  list_access_entities: ['entity'], get_access_entity: ['entity', 'id'], get_requestable_roles: [],
  ...Object.fromEntries(Object.entries(MANAGEMENT).map(([name, value]) => [name, value.keys])),
};
const CONCEPTS = ['overview', 'permission', 'role', 'policy', 'resource'];
const messages = {
  AUTH: '인증할 수 없습니다. 로그인이 만료되었거나 유효하지 않습니다. 다시 로그인하세요.',
  LOGIN_INPUT: '사용자 이름은 영문·숫자·_·- 1–64자, 비밀번호는 1–256자로 입력하세요.',
  LOGIN_CREDENTIALS: '사용자 이름 또는 비밀번호를 확인하세요.',
  LOGIN_UNAVAILABLE: '로그인 서비스를 사용할 수 없습니다. 잠시 후 다시 시도하세요.',
  LOGIN_NETWORK: '로그인 서버에 접속할 수 없습니다. 연결 상태를 확인하세요.',
  LOGIN_PROTOCOL: '로그인 응답을 확인할 수 없습니다. 서버 관리자에게 확인하세요.',
  FORBIDDEN: '요청 권한이 부족합니다. 조회는 authz.read, 변경은 authz.manage와 서버의 관리자 권한이 필요하며 보호된 관계는 변경할 수 없습니다.',
  NOT_FOUND: '대상을 조회하거나 변경할 수 없습니다. 보이지 않는 대상과 없는 대상은 구분하지 않습니다.',
  TARGET: '작업과 대상 ID를 정확히 입력하세요. 사용자·역할·정책 ID는 영문/숫자/_/- 1–64자, 리소스 ID는 R과 숫자 3자리입니다.',
  INVALID_ARGUMENT: '조회 인수가 올바르지 않습니다. 예: 리소스 R100에 접근할 수 있어? 왜?',
  ACCESS_DENIED: '현재 로그인한 사용자로 조회할 수 없습니다. 서버 관리자에게 확인하세요.',
  UPSTREAM_ACCESS_DENIED: '권한 API 접근이 거부되었습니다. 서버 관리자에게 확인해 주세요.',
  UPSTREAM_UNAVAILABLE: '권한 API가 응답하지 않습니다. 잠시 후 다시 조회하세요.',
  UPSTREAM_INVALID_RESPONSE: '권한 API 응답을 확인할 수 없습니다. 서버 관리자에게 확인해 주세요.',
  NETWORK: 'MCP 서버에 접속할 수 없습니다. 서버 실행 상태를 확인하세요.',
  TIMEOUT: '12초 안에 응답하지 않아 요청을 중단했습니다. 다시 시도해 주세요.',
  CANCELLED: '요청을 취소했습니다.',
  PROTOCOL: 'MCP 응답 형식이나 권한 관계가 올바르지 않습니다. 서버 설정을 확인하세요.',
  TOOL: '권한 도구 실행에 실패했습니다. 잠시 후 다시 시도하세요.',
  MISSING_TOOL: '서버의 자기권한 조회 도구 또는 입력 계약을 확인할 수 없습니다.',
  SERVER: 'MCP 서버가 요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.',
  INPUT: '지원하는 질문을 입력하세요: 내 정보 알려줘 / 내 역할은 뭐야? / 정책 목록 / 사용자 U100 상세 / 요청 가능한 역할 알려줘 / 리소스 R100에 접근할 수 있어? 왜? / 역할과 정책은 어떻게 달라? 관계 변경은 관리 양식에서 미리보기 후 실행하세요.',
  RESOURCE_ID: '리소스 ID 하나를 입력하세요. 형식은 R과 숫자 3자리입니다. 예: 리소스 R100에 접근할 수 있어? 왜?',
  RESOURCE_NAME: '이름만으로 리소스를 특정할 수 없습니다. 리소스 ID로 질문해 주세요. 예: 리소스 R100에 접근할 수 있어? 왜?',
};
export class SafeError extends Error {
  constructor(code) { super(messages[code] || messages.SERVER); this.code = code; }
}
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = () => { throw new SafeError('PROTOCOL'); };
const string = (value, max = 2000) => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const identifier = value => string(value, 128) && !/[\s\u0000-\u001f\u007f]/u.test(value);
const catalogId = value => typeof value === 'string' && value.length >= 1 && value.length <= 64 && !/[^A-Za-z0-9_-]/.test(value);
const resourceId = value => typeof value === 'string' && /^R[0-9]{3}$/.test(value);
const sameSet = (a, b) => a.length === b.length && new Set(a).size === a.length && a.every(value => b.includes(value));
export function loginResult(value) {
  const bearer = value => typeof value === 'string' && value.length <= 8192 && value.split('.').length === 3
    && value.split('.').every(part => part.length > 0 && !/[^A-Za-z0-9_-]/.test(part));
  if (!object(value) || !sameSet(Object.keys(value), ['tokenType', 'expiresIn', 'mcpAccessToken', 'apiAccessToken', 'user'])
      || value.tokenType !== 'Bearer' || !Number.isInteger(value.expiresIn) || value.expiresIn < 60 || value.expiresIn > 900
      || !bearer(value.mcpAccessToken) || !bearer(value.apiAccessToken) || value.mcpAccessToken === value.apiAccessToken
      || !object(value.user) || !sameSet(Object.keys(value.user), ['id', 'name']) || !catalogId(value.user.id) || !string(value.user.name, 200)) {
    throw new SafeError('LOGIN_PROTOCOL');
  }
  // The API token and user metadata are not retained. Display identity comes from get_my_access.
  return { mcpAccessToken: value.mcpAccessToken, expiresIn: value.expiresIn };
}
export async function readLoginResponse(response) {
  const protocolError = () => { throw new SafeError('LOGIN_PROTOCOL'); };
  if (![200, 400, 401, 503].includes(response.status)) {
    await response.body?.cancel(); throw new SafeError('LOGIN_UNAVAILABLE');
  }
  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json' || !response.body) {
    await response.body?.cancel(); protocolError();
  }
  const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0, body = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (value) {
        bytes += value.byteLength;
        if (bytes > MAX_LOGIN_RESPONSE) protocolError();
        body += decoder.decode(value, { stream: true });
      }
      if (done) break;
    }
    body += decoder.decode();
    const data = JSON.parse(body);
    if (response.status === 200) return loginResult(data);
    const errors = { 400: ['INVALID_ARGUMENT', 'LOGIN_INPUT'], 401: ['INVALID_CREDENTIALS', 'LOGIN_CREDENTIALS'], 503: ['AUTH_UNAVAILABLE', 'LOGIN_UNAVAILABLE'] };
    const [code, message] = errors[response.status];
    if (!object(data) || !sameSet(Object.keys(data), ['code']) || data.code !== code) protocolError();
    throw new SafeError(message);
  } catch (error) {
    if (error instanceof SafeError || error?.name === 'AbortError') throw error;
    protocolError();
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
function uniqueStrings(value) {
  if (!Array.isArray(value) || value.length > 200 || !value.every(identifier) || new Set(value).size !== value.length) fail();
  return [...value];
}
function entity(value, resource = false) {
  if (!object(value) || !(resource ? resourceId(value.id) : identifier(value.id)) || !string(value.name, 200) || typeof value.description !== 'string' || value.description.length > 2000) fail();
  return { id: value.id, name: value.name, description: value.description };
}
function entities(values, mapper) {
  if (!Array.isArray(values) || values.length > 200) fail();
  const result = values.map(mapper);
  if (new Set(result.map(value => value.id)).size !== result.length) fail();
  return result;
}
function grants(values) {
  if (!Array.isArray(values) || values.length > 1000) fail();
  const result = values.map(value => {
    if (!object(value) || !identifier(value.roleId) || !identifier(value.policyId)) fail();
    return { roleId: value.roleId, policyId: value.policyId };
  });
  if (new Set(result.map(value => JSON.stringify([value.roleId, value.policyId]))).size !== result.length) fail();
  return result;
}
export function classifyQuestion(text) {
  if (typeof text !== 'string' || text.length > 300) throw new SafeError('INPUT');
  const compact = text.trim().replace(/[\s?？!.。]/gu, '');
  const views = [
    ['profile', /^(내정보(알려줘|보여줘)?|나는누구야)$/u, '내 정보 알려줘'],
    ['roles', /^(내역할(은뭐야|알려줘|보여줘)?|나의역할(은뭐야|알려줘)?)$/u, '내 역할은 뭐야?'],
    ['policies', /^(나에게적용된정책(은|알려줘)?|내정책(은뭐야|알려줘|보여줘)?)$/u, '나에게 적용된 정책은?'],
    ['resources', /^(접근가능한리소스(알려줘|보여줘)?|내리소스(알려줘|보여줘)?)$/u, '접근 가능한 리소스 알려줘'],
    ['relationships', /^내권한관계(알려줘|보여줘)?$/u, '내 권한 관계 보여줘'],
  ];
  for (const [view, pattern, label] of views) if (pattern.test(compact)) return { kind: 'access', view, label };
  if (/^요청가능한역할(알려줘|보여줘)?$/u.test(compact)) return { kind: 'requestable', label: '요청 가능한 역할 알려줘' };
  for (const [label, kind] of [['사용자', 'users'], ['역할', 'roles'], ['정책', 'policies'], ['리소스', 'resources']]) {
    if (new RegExp(`^(카탈로그)?${label}목록(알려줘|보여줘)?$`, 'u').test(compact)) return { kind: 'catalog', entity: kind, label: `${label} 목록` };
    const detail = new RegExp(`^${label}\\s+([A-Za-z0-9_-]{1,64})\\s+상세(?:\\s*(?:알려줘|보여줘))?[?？!.。]*$`, 'u').exec(text.trim());
    if (detail) {
      if (kind === 'resources' && !resourceId(detail[1])) throw new SafeError('RESOURCE_ID');
      return { kind: 'detail', entity: kind, id: detail[1], label: `${label} ${detail[1]} 상세` };
    }
  }
  if (/^(역할과정책은어떻게달라|권한구조(설명해줘|알려줘)|사용자역할정책리소스관계(알려줘|설명해줘))$/u.test(compact)) return { kind: 'concept', concept: 'overview', label: '역할과 정책은 어떻게 달라?' };
  for (const [name, concept] of [['권한', 'permission'], ['역할', 'role'], ['정책', 'policy'], ['리소스', 'resource']]) {
    if (new RegExp(`^${name}(이뭐야|가뭐야|이란|란|설명해줘)$`, 'u').test(compact)) return { kind: 'concept', concept, label: `${name} 개념 설명` };
  }
  const trimmed = text.trim();
  // Only question suffix punctuation is syntax. Preserve the captured resource name exactly.
  const match = /^리소스\s+(.+?)(?:에)?\s*접근\s*(?:할\s*수\s*있어|가능해|가능\s*여부|이유)\s*[?？!.。]*(?:\s*왜\s*[?？!.。]*)?$/iu.exec(trimmed);
  if (match) {
    const target = match[1].trim();
    if (/^R\d{3}$/iu.test(target)) return { kind: 'resource', resourceId: target.toUpperCase(), label: `리소스 ${target.toUpperCase()} 접근 여부와 이유` };
    if (/^R\d/iu.test(target) || /(?:^|\s)R\d/iu.test(target)) throw new SafeError('RESOURCE_ID');
    if (!string(target, 200)) throw new SafeError('RESOURCE_ID');
    return { kind: 'resource-name', name: target, label: '이름으로 리소스 접근 여부 확인' };
  }
  throw new SafeError('INPUT');
}
export function rpcResult(payload, id) {
  if (!object(payload) || payload.jsonrpc !== '2.0' || payload.id !== id
      || (Object.hasOwn(payload, 'result') === Object.hasOwn(payload, 'error'))) fail();
  if (Object.hasOwn(payload, 'error')) {
    if (!object(payload.error) || !Number.isInteger(payload.error.code)) fail();
    throw new SafeError('TOOL');
  }
  if (!object(payload.result)) fail();
  return payload.result;
}
function parseJson(text) {
  try { return JSON.parse(text); } catch { fail(); }
}
const canonical = value => JSON.stringify(value, function (_key, item) {
  return object(item) ? Object.keys(item).sort().reduce((copy, key) => { Object.defineProperty(copy, key, { value: item[key], enumerable: true }); return copy; }, {}) : item;
});
export function toolData(result) {
  if (!object(result) || (result.isError !== undefined && typeof result.isError !== 'boolean')) fail();
  if (result.isError) {
    const code = Array.isArray(result.content) && result.content.length === 1 && result.content[0]?.type === 'text' ? result.content[0].text : '';
    const safeCodes = ['FORBIDDEN', 'NOT_FOUND', 'INVALID_ARGUMENT', 'ACCESS_DENIED', 'UPSTREAM_ACCESS_DENIED', 'UPSTREAM_UNAVAILABLE', 'UPSTREAM_INVALID_RESPONSE'];
    throw new SafeError(safeCodes.includes(code) ? code : 'TOOL');
  }
  let textData;
  if (result.content !== undefined) {
    if (!Array.isArray(result.content) || result.content.length > 1) fail();
    if (result.content.length) {
      if (result.content[0]?.type !== 'text' || typeof result.content[0].text !== 'string') fail();
      textData = parseJson(result.content[0].text);
    }
  }
  if (Object.hasOwn(result, 'structuredContent')) {
    if (!object(result.structuredContent) || (textData !== undefined && canonical(textData) !== canonical(result.structuredContent))) fail();
    return result.structuredContent;
  }
  if (!object(textData)) fail();
  return textData;
}
export function accessResult(result) {
  const data = toolData(result);
  const user = { ...entity(data.user), roleIds: uniqueStrings(data.user.roleIds) };
  const roles = entities(data.roles, value => ({ ...entity(value), policyIds: uniqueStrings(value.policyIds) }));
  const policies = entities(data.policies, value => ({ ...entity(value), resourceIds: uniqueStrings(value.resourceIds), grantedByRoleIds: uniqueStrings(value.grantedByRoleIds) }));
  const resources = entities(data.resources, value => ({ ...entity(value, true), grants: grants(value.grants) }));
  if (!sameSet(user.roleIds, roles.map(role => role.id))) fail();
  const policyMap = new Map(policies.map(policy => [policy.id, policy]));
  const resourceMap = new Map(resources.map(resource => [resource.id, resource]));
  for (const role of roles) if (role.policyIds.some(id => !policyMap.has(id))) fail();
  for (const policy of policies) {
    const expected = roles.filter(role => role.policyIds.includes(policy.id)).map(role => role.id);
    if (!expected.length || !sameSet(policy.grantedByRoleIds, expected) || policy.resourceIds.some(id => !resourceMap.has(id))) fail();
  }
  for (const resource of resources) {
    const expected = policies.flatMap(policy => policy.resourceIds.includes(resource.id) ? policy.grantedByRoleIds.map(roleId => JSON.stringify([roleId, policy.id])) : []);
    if (!expected.length || !sameSet(resource.grants.map(grant => JSON.stringify([grant.roleId, grant.policyId])), expected)) fail();
  }
  return { user, roles, policies, resources };
}
export function resourceResult(result, id) {
  const data = toolData(result);
  if (!resourceId(id) || data.resourceId !== id || typeof data.allowed !== 'boolean') fail();
  const paths = grants(data.grants);
  if (!data.allowed) {
    if (data.resource !== null || paths.length) fail();
    return { resourceId: id, allowed: false, resource: null, grants: [] };
  }
  const resource = entity(data.resource, true);
  if (resource.id !== id || !paths.length) fail();
  return { resourceId: id, allowed: true, resource, grants: paths };
}
export function conceptResult(result, concept) {
  const data = toolData(result);
  if (!CONCEPTS.includes(concept) || data.concept !== concept || !string(data.explanation, 4000)) fail();
  return { concept, explanation: data.explanation };
}
export function resolveResourceName(access, name) {
  const matches = access.resources.filter(resource => resource.name === name);
  if (matches.length !== 1) throw new SafeError('RESOURCE_NAME');
  return matches[0].id;
}
function catalogItem(value, kind) {
  if (!CATALOGS.includes(kind)) fail();
  const base = entity(value, kind === 'resources');
  if (!catalogId(base.id)) fail();
  const ids = key => {
    const values = uniqueStrings(value[key]);
    if (!values.every(key === 'resourceIds' ? resourceId : catalogId)) fail();
    return values;
  };
  if (kind === 'users') return { ...base, roleIds: ids('roleIds') };
  if (typeof value.protected !== 'boolean') fail();
  if (kind === 'roles') {
    if (typeof value.requestable !== 'boolean' || (value.protected && value.requestable)) fail();
    return { ...base, policyIds: ids('policyIds'), requestable: value.requestable, protected: value.protected };
  }
  if (kind === 'policies') return { ...base, resourceIds: ids('resourceIds'), protected: value.protected };
  return { ...base, protected: value.protected };
}
export function catalogResult(result, kind, id) {
  const data = toolData(result);
  if (!CATALOGS.includes(kind) || data.entity !== kind) fail();
  if (id !== undefined) {
    const item = catalogItem(data.item, kind);
    if (item.id !== id) fail();
    return { entity: kind, item };
  }
  return { entity: kind, items: entities(data.items, value => catalogItem(value, kind)) };
}
export function requestableResult(result) {
  const data = toolData(result);
  return { roles: entities(data.roles, value => {
    const role = entity(value);
    if (!catalogId(role.id) || !sameSet(Object.keys(value), ['id', 'name', 'description'])) fail();
    return role;
  }) };
}
export function requestableText(data) {
  return `요청 가능한 역할 · 공개 메타데이터, 현재 권한 아님\n\n${data.roles.length ? data.roles.map(role => `${role.name} (${role.id})\n${role.description}`).join('\n\n') : '요청 가능한 역할이 없습니다.'}\n\n여기에는 신청·승인 기능이 없습니다. 직접 보유 역할 여부를 기준으로 서버가 목록을 결정합니다.`;
}
export function managementArgs(name, first, second) {
  if (!Object.hasOwn(MANAGEMENT, name)) throw new SafeError('TARGET');
  const keys = MANAGEMENT[name].keys;
  const values = [first, second];
  if (values.some((value, index) => !(keys[index] === 'resourceId' ? resourceId(value) : catalogId(value)))) throw new SafeError('TARGET');
  return Object.fromEntries(keys.map((key, index) => [key, values[index]]));
}
export function mutationResult(result, name, args) {
  const data = toolData(result);
  if (!Object.hasOwn(MANAGEMENT, name) || data.operation !== name || typeof data.changed !== 'boolean'
      || data.present !== MANAGEMENT[name].present || !object(data.relationship)
      || !sameSet(Object.keys(data.relationship), MANAGEMENT[name].keys)
      || MANAGEMENT[name].keys.some(key => data.relationship[key] !== args[key]) || !object(data.impact)) fail();
  const affectedUserIds = uniqueStrings(data.impact.affectedUserIds);
  if (!affectedUserIds.every(catalogId) || (Object.hasOwn(args, 'userId') && !sameSet(affectedUserIds, [args.userId]))) fail();
  return { operation: name, changed: data.changed, relationship: { ...args }, present: data.present, impact: { affectedUserIds } };
}
export function catalogText(data) {
  const items = data.items ?? [data.item];
  return `${data.entity} · 서버에서 허용한 카탈로그 범위\n\n${items.length ? items.map(item => {
    const details = [item.roleIds && `역할: ${item.roleIds.join(', ') || '없음'}`, item.policyIds && `정책: ${item.policyIds.join(', ') || '없음'}`, item.resourceIds && `리소스: ${item.resourceIds.join(', ') || '없음'}`].filter(Boolean);
    if (item.protected !== undefined) details.push(`보호됨: ${item.protected ? '예' : '아니요'}`);
    if (item.requestable !== undefined) details.push(`요청 가능 설정: ${item.requestable ? '예' : '아니요'}`);
    return `${item.name} (${item.id})\n${item.description}\n${details.join('\n')}`;
  }).join('\n\n') : '조회 가능한 항목이 없습니다.'}`;
}
export function validateTools(list) {
  if (!object(list) || !Array.isArray(list.tools)) fail();
  for (const [name, keys] of Object.entries(TOOL_KEYS)) {
    const found = list.tools.filter(tool => object(tool) && tool.name === name);
    if (found.length !== 1) throw new SafeError('MISSING_TOOL');
    const schema = found[0].inputSchema;
    if (!object(schema) || schema.type !== 'object' || schema.additionalProperties !== false || !object(schema.properties)
        || !sameSet(Object.keys(schema.properties), keys) || !Array.isArray(schema.required ?? [])
        || !sameSet(schema.required ?? [], keys) || keys.some(key => schema.properties[key]?.type !== 'string')) throw new SafeError('MISSING_TOOL');
    for (const key of keys) {
      const property = schema.properties[key];
      if (key === 'concept' || key === 'entity') {
        if (!Array.isArray(property.enum) || !sameSet(property.enum, key === 'concept' ? CONCEPTS : CATALOGS)) throw new SafeError('MISSING_TOOL');
      } else if (property.pattern !== (key === 'resourceId' ? '^R[0-9]{3}$' : ID_PATTERN)) throw new SafeError('MISSING_TOOL');
    }
    const mutation = Object.hasOwn(MANAGEMENT, name);
    const annotations = found[0].annotations;
    if (!object(annotations) || annotations.readOnlyHint !== !mutation
        || (mutation && (annotations.destructiveHint !== true || annotations.idempotentHint !== true))) throw new SafeError('MISSING_TOOL');
  }
}
export function accessText(access, view) {
  const title = `${access.user.name} (${access.user.id}) · 로그인한 사용자의 실제 권한`;
  const roleName = id => { const role = access.roles.find(item => item.id === id); return `${role.name} (${id})`; };
  const policyName = id => { const policy = access.policies.find(item => item.id === id); return `${policy.name} (${id})`; };
  if (view === 'profile') return `${title}\n${access.user.description}\n\n역할 ${access.roles.length}개 · 정책 ${access.policies.length}개 · 접근 가능한 리소스 ${access.resources.length}개\n신원은 서버가 검증한 로그인 토큰으로 결정됩니다.`;
  if (view === 'roles') return `${title}\n\n${access.roles.length ? access.roles.map(role => `${role.name} (${role.id})\n${role.description}\n연결 정책: ${role.policyIds.map(policyName).join(', ') || '없음'}`).join('\n\n') : '부여된 역할이 없습니다.'}`;
  if (view === 'policies') return `${title}\n\n${access.policies.length ? access.policies.map(policy => `${policy.name} (${policy.id})\n${policy.description}\n부여 역할: ${policy.grantedByRoleIds.map(roleName).join(', ')}\n허용 리소스: ${policy.resourceIds.join(', ') || '없음'}`).join('\n\n') : '적용된 정책이 없습니다.'}`;
  return `${title}\n\n${access.resources.length ? access.resources.map(resource => `${resource.name} (${resource.id})\n${resource.description}\n권한 경로:\n${resource.grants.map(grant => `${roleName(grant.roleId)} → ${policyName(grant.policyId)} → ${resource.id}`).join('\n')}`).join('\n\n') : '접근 가능한 리소스가 없습니다.'}`;
}
export async function readResponse(response, id) {
  const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (!['application/json', 'text/event-stream'].includes(type) || !response.body) {
    if (response.body) await response.body.cancel().catch(() => {});
    throw new SafeError('PROTOCOL');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0, buffer = '';
  const eventResult = event => {
    const data = event.split(/\r\n|\r|\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n');
    if (!data) return undefined;
    const payload = parseJson(data);
    if (object(payload) && payload.jsonrpc === '2.0' && typeof payload.method === 'string'
        && payload.method.startsWith('notifications/') && !Object.hasOwn(payload, 'id')) return undefined;
    return rpcResult(payload, id);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (value) {
        bytes += value.byteLength;
        if (bytes > MAX_RESPONSE) throw new SafeError('PROTOCOL');
        buffer += decoder.decode(value, { stream: true });
      }
      if (done) buffer += decoder.decode();
      if (type === 'text/event-stream') {
        let boundary;
        while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer))) {
          const result = eventResult(buffer.slice(0, boundary.index));
          buffer = buffer.slice(boundary.index + boundary[0].length);
          if (result !== undefined) return result;
        }
        if (done && buffer.trim()) { const result = eventResult(buffer); if (result !== undefined) return result; }
      }
      if (done) break;
    }
    if (type === 'application/json') return rpcResult(parseJson(buffer), id);
    throw new SafeError('PROTOCOL');
  } catch (error) {
    if (error instanceof SafeError || error?.name === 'AbortError') throw error;
    throw new SafeError('PROTOCOL');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  const tokenInput = $('token'), query = $('query'), loginDialog = $('login-dialog'), loginPassword = $('login-password');
  let token = '', ready = false, generation = 0, sequence = 0, active = null, preview = null, previewRevision = 0;
  let expiresAt = 0, expiryTimer = null;
  let browseCatalog = null, targetCatalogs = null;
  const catalogLabels = { users: '사용자', roles: '역할', policies: '정책', resources: '리소스' };
  const keyCatalog = { userId: 'users', roleId: 'roles', policyId: 'policies', resourceId: 'resources' };
  const unknownOutcome = '결과 미확인: 이미 전송한 변경은 서버에서 완료될 수 있습니다. 취소·연결 해제는 롤백이 아니며 자동으로 변경을 재시도하지 않습니다.';
  $('manage-operation').value = 'assign_role_to_user';
  $('browse-entity').value = 'roles';
  function options(id, items, placeholder, protect = false) {
    const select = $(id);
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = placeholder;
    select.replaceChildren(empty); select.value = '';
    for (const item of items) {
      const option = document.createElement('option'); option.value = item.id;
      option.textContent = `${item.name} (${item.id})${protect && item.protected ? ' · 보호됨, 선택 불가' : ''}`;
      option.disabled = protect && item.protected === true;
      select.append(option);
    }
  }
  function clearTargets() {
    targetCatalogs = null;
    options('manage-first-select', [], '목록을 먼저 불러오세요');
    options('manage-second-select', [], '목록을 먼저 불러오세요');
    $('manage-target-status').textContent = '현재 작업에 맞는 목록을 불러오거나 검증할 ID를 직접 입력하세요.';
  }
  function clearDiscovery() {
    browseCatalog = null;
    options('browse-item', [], '먼저 목록을 불러오세요'); $('browse-id').value = '';
    $('catalog-browser').hidden = true; $('browse-result').textContent = ''; $('browse-status').textContent = '';
    $('relationship-view').hidden = true; $('relationship-view').open = false;
    $('relationship-summary').textContent = ''; $('relationship-roles').textContent = ''; $('relationship-paths').replaceChildren();
    clearTargets();
  }
  function ensureConnected() {
    if (expiresAt && Date.now() >= expiresAt) requireLogin('로그인 시간이 만료되었습니다. 다시 로그인하세요.');
    if (!ready) {
      if (active) return false;
      $('discovery-status').textContent = '이 기능을 사용하려면 로그인하세요. 로그인 후 선택한 기능을 다시 실행할 수 있습니다.';
      $('connection-note').textContent = '로그인 후 질문을 보내거나 선택한 기능을 다시 실행하세요.';
      openLogin(); return false;
    }
    if (active) { $('discovery-status').textContent = '요청 처리 중입니다. 완료하거나 취소한 뒤 다시 선택하세요.'; return false; }
    return true;
  }
  function renderRelationships(graph, expand = false) {
    $('relationship-view').hidden = false;
    if (expand) $('relationship-view').open = true;
    $('relationship-summary').textContent = `${graph.user.name} (${graph.user.id}) · 역할 ${graph.roles.length}개 → 정책 ${graph.policies.length}개 → 리소스 ${graph.resources.length}개`;
    $('relationship-roles').textContent = `내 역할: ${graph.roles.map(role => `${role.name} (${role.id})`).join(', ') || '없음'}\n아래 경로는 서버에서 확인한 실제 부여 관계입니다.`;
    const paths = $('relationship-paths'); paths.replaceChildren();
    const roles = new Map(graph.roles.map(role => [role.id, role]));
    const policies = new Map(graph.policies.map(policy => [policy.id, policy]));
    for (const resource of graph.resources) for (const grant of resource.grants) {
      const path = document.createElement('li');
      path.textContent = `사용자 ${graph.user.name} (${graph.user.id}) → 역할 ${roles.get(grant.roleId).name} (${grant.roleId}) → 정책 ${policies.get(grant.policyId).name} (${grant.policyId}) → 리소스 ${resource.name} (${resource.id})`;
      paths.append(path);
    }
    if (!graph.resources.length) {
      const empty = document.createElement('li'); empty.textContent = '리소스까지 이어지는 허용 경로가 없습니다. 역할·정책 수는 위 요약을 확인하세요.'; paths.append(empty);
    }
  }
  function renderCatalog(data) {
    $('catalog-browser').hidden = false;
    $('browse-entity').value = data.entity;
    if (data.items) {
      browseCatalog = { generation, entity: data.entity, items: data.items };
      options('browse-item', data.items, data.items.length ? '항목을 선택하세요' : '조회 가능한 항목이 없습니다');
      $('browse-id').value = '';
      $('browse-status').textContent = `${catalogLabels[data.entity]} ${data.items.length}개 · 서버가 허용한 범위만 표시합니다.`;
    } else {
      if (browseCatalog?.entity !== data.entity) {
        browseCatalog = null; options('browse-item', [], '이 종류의 목록을 불러오세요');
      }
      $('browse-id').value = data.item.id;
      $('browse-status').textContent = `${catalogLabels[data.entity]} 상세 · 서버가 허용한 항목입니다.`;
    }
    $('browse-result').textContent = catalogText(data);
  }
  function clearPreview(clearFields = false) {
    preview = null; previewRevision++;
    $('manage-preview').hidden = true;
    $('manage-actor').textContent = ''; $('manage-summary').textContent = '';
    if (clearFields) { $('manage-first').value = ''; $('manage-second').value = ''; }
  }
  function managementLabels() {
    const labels = { userId: '대상 사용자 ID', roleId: '대상 역할 ID', policyId: '대상 정책 ID', resourceId: '대상 리소스 ID' };
    const keys = MANAGEMENT[$('manage-operation').value]?.keys ?? ['userId', 'roleId'];
    $('manage-first-label').textContent = labels[keys[0]]; $('manage-second-label').textContent = labels[keys[1]];
    $('manage-first-select-label').textContent = `목록에서 ${labels[keys[0]]} 선택`;
    $('manage-second-select-label').textContent = `목록에서 ${labels[keys[1]]} 선택`;
  }
  function refresh() {
    $('connect').disabled = ready || !!active;
    $('login-open').disabled = ready || !!active;
    $('login-open').hidden = ready;
    $('login-submit').disabled = !!active || ready;
    $('login-username').disabled = !!active;
    loginPassword.disabled = !!active;
    $('login-form').setAttribute('aria-busy', String(active?.kind === 'login'));
    $('disconnect').disabled = !ready && !active && !token;
    $('send').disabled = !!active;
    query.disabled = !!active;
    query.placeholder = ready ? '예: 내 역할은 뭐야?' : '질문을 입력하면 로그인으로 안내합니다';
    $('cancel').hidden = !active;
    document.querySelectorAll('[data-query]').forEach(button => { button.disabled = !!active; });
    $('connection-state').textContent = ready ? '연결됨' : active ? '연결 중' : '연결 안 됨';
    $('connection-state').dataset.state = ready ? 'ready' : active ? 'busy' : 'idle';
    $('activity').textContent = active ? (ready ? '서버에서 요청을 처리하고 있습니다…' : active.kind === 'login' ? '로그인과 MCP 연결을 확인하고 있습니다…' : 'MCP 연결을 확인하고 있습니다…') : ready ? '조회 범위와 관리 권한은 서버에서 검증합니다.' : '질문을 입력하거나 로그인하세요.';
    for (const id of ['manage-operation', 'manage-first', 'manage-second', 'manage-preview-button', 'refresh-access']) $(id).disabled = !ready || !!active;
    $('manage-execute').disabled = !ready || !!active || !preview;
    $('manage-cancel').disabled = !preview && !['preview', 'mutation', 'refresh', 'targets'].includes(active?.kind);
    for (const id of ['discover-own', 'discover-browse', 'discover-requestable', 'discover-manage', 'discover-concept', 'browse-entity', 'manage-load-targets']) $(id).disabled = !!active;
    $('browse-item').disabled = !ready || !!active || !browseCatalog?.items.length;
    $('browse-id').disabled = !ready || !!active;
    $('browse-detail').disabled = !ready || !!active;
    for (const [id, key] of [['manage-first-select', 'first'], ['manage-second-select', 'second']]) $(id).disabled = !ready || !!active || !targetCatalogs?.[key].some(item => !item.protected);
    $('manage-eligibility').textContent = !ready ? '연결이 필요합니다. 관리 카드를 눌러 연결 방법을 확인하세요.' : active ? '처리 중에는 새 변경을 실행할 수 없습니다.' : '미리보기에는 조회 권한이 필요합니다. 실행 시 서버가 변경 권한·DB 관리자·보호된 대상을 검사합니다.';
    $('messages').setAttribute('aria-busy', String(!!active));
    $('capabilities').setAttribute('aria-busy', String(!!active));
    managementLabels();
  }
  function invalidate(clearInput = true, keepDraft = false) {
    generation++;
    clearTimeout(expiryTimer); expiryTimer = null; expiresAt = 0;
    clearTimeout(active?.timer);
    active?.controller.abort();
    active = null; token = ''; ready = false;
    loginPassword.value = '';
    if (clearInput) tokenInput.value = '';
    if (!keepDraft) query.value = '';
    $('messages').replaceChildren();
    clearDiscovery();
    $('discovery-status').textContent = '로그인 정보와 이전 조회 결과를 지웠습니다. 로그인한 뒤 다시 조회하세요.';
    clearPreview(true);
    $('manage-status').textContent = '연결 후 새 미리보기가 필요합니다.';
    refresh();
  }
  function openLogin(note = '로그인이 필요합니다. 로그인 후 작성 중인 질문을 보낼 수 있습니다.') {
    if (ready || active) return;
    loginPassword.value = '';
    $('login-status').textContent = note;
    if (!loginDialog.open) loginDialog.showModal();
    $('login-username').focus();
  }
  function closeLogin() {
    loginPassword.value = '';
    if (loginDialog.open) loginDialog.close();
  }
  function requireLogin(note) {
    const sent = active?.sentMutation;
    invalidate(true, true);
    const message = `${note}${sent && !note.includes(unknownOutcome) ? ` ${unknownOutcome} 로그인 후 최신 상태를 조회하세요.` : ''}`;
    $('connection-note').textContent = message;
    openLogin(message);
  }
  function scheduleExpiry(lifetime, startedAt) {
    expiresAt = startedAt + lifetime * 1000;
    const connectionGeneration = generation;
    expiryTimer = setTimeout(() => {
      if (connectionGeneration === generation) requireLogin('로그인 시간이 만료되었습니다. 다시 로그인하세요.');
    }, Math.max(0, expiresAt - Date.now()));
  }
  function bubble(text, role = 'assistant', error = false) {
    const article = document.createElement('article'); article.className = `message ${role}${error ? ' error' : ''}`;
    if (role === 'assistant') { const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = '▦'; avatar.setAttribute('aria-hidden', 'true'); article.append(avatar); }
    const body = document.createElement('div');
    const author = document.createElement('span'); author.className = 'message-author'; author.textContent = role === 'user' ? '나' : '권한 도우미';
    const content = document.createElement('div'); content.className = 'bubble'; content.textContent = text;
    body.append(author, content); article.append(body); $('messages').append(article);
    article.scrollIntoView({ block: 'nearest' });
    return content;
  }
  function begin(kind = 'read') {
    const operation = { generation, kind, controller: new AbortController(), timedOut: false };
    operation.timer = setTimeout(() => { operation.timedOut = true; operation.controller.abort(); }, TIMEOUT);
    $('discovery-status').textContent = '서버에서 요청을 처리하고 있습니다…';
    $('discovery-status').dataset.state = 'loading';
    active = operation; refresh(); return operation;
  }
  const current = operation => operation.generation === generation && active === operation;
  function finish(operation) {
    clearTimeout(operation.timer);
    if (current(operation)) {
      active = null;
      if ($('discovery-status').dataset.state === 'loading') {
        $('discovery-status').dataset.state = 'idle';
        $('discovery-status').textContent = '요청 처리를 마쳤습니다. 결과 또는 안내를 확인하세요.';
      }
      refresh();
    }
  }
  async function rpc(operation, method, params, notification = false) {
    if (!current(operation)) throw new SafeError('CANCELLED');
    if (expiresAt && Date.now() >= expiresAt) { requireLogin('로그인 시간이 만료되었습니다. 다시 로그인하세요.'); throw new SafeError('AUTH'); }
    operation.controller.signal.throwIfAborted();
    const id = ++sequence;
    const body = { jsonrpc: '2.0', method, ...(notification ? {} : { id }), ...(params ? { params } : {}) };
    let response;
    try {
      response = await fetch('/mcp', { method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', Authorization: `Bearer ${token}`, 'MCP-Protocol-Version': PROTOCOL },
        body: JSON.stringify(body), signal: operation.controller.signal });
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw new SafeError('NETWORK');
    }
    if (!current(operation)) { await response.body?.cancel(); throw new SafeError('CANCELLED'); }
    if (!response.ok) {
      await response.body?.cancel();
      throw new SafeError(response.status === 401 ? 'AUTH' : response.status === 403 ? 'FORBIDDEN' : 'SERVER');
    }
    if (notification) {
      await response.body?.cancel();
      if (![200, 202, 204].includes(response.status)) throw new SafeError('PROTOCOL');
      return;
    }
    const result = await readResponse(response, id);
    operation.controller.signal.throwIfAborted();
    return result;
  }
  function safeFailure(error, operation) {
    return operation.timedOut ? new SafeError('TIMEOUT') : error?.name === 'AbortError' ? new SafeError('CANCELLED') : error instanceof SafeError ? error : new SafeError('SERVER');
  }
  async function connectMcp(operation) {
    $('connection-note').textContent = '서버 초기화와 권한 도구 12개를 확인하고 있습니다.';
    const init = await rpc(operation, 'initialize', { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: { name: 'access-chat', version: '1.0.0' } });
    if (!current(operation)) return;
    if (init.protocolVersion !== PROTOCOL || !object(init.capabilities) || !object(init.serverInfo)) throw new SafeError('PROTOCOL');
    await rpc(operation, 'notifications/initialized', undefined, true);
    if (!current(operation)) return;
    const list = await rpc(operation, 'tools/list', {});
    if (!current(operation)) return;
    validateTools(list);
    ready = true;
    tokenInput.value = '';
    $('connection-note').textContent = '권한 도구 12개를 확인했습니다. 질문을 보내거나 관리 양식에서 미리보기를 만드세요.';
    bubble('MCP 서버에 연결했습니다. 내 권한이나 조회 가능한 카탈로그를 질문하세요. 관계 변경은 관리 양식에서 미리보기 후 실행합니다.');
  }
  $('login-open').addEventListener('click', () => openLogin());
  const cancelLogin = () => {
    if (active?.kind === 'login') invalidate(true, true);
    closeLogin();
    $('connection-note').textContent = '로그인을 취소했습니다. 질문을 보내려면 다시 로그인하세요.';
    query.focus();
  };
  $('login-cancel').addEventListener('click', cancelLogin);
  loginDialog.addEventListener('cancel', event => { event.preventDefault(); cancelLogin(); });
  loginDialog.addEventListener('close', () => {
    // Ignore an earlier close event if a new login dialog has already opened.
    if (loginDialog.open) return;
    loginPassword.value = '';
    if (active?.kind === 'login' && !ready) cancelLogin();
  });
  $('login-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (active || ready) return;
    const username = $('login-username').value;
    let password = loginPassword.value;
    loginPassword.value = '';
    if (!catalogId(username) || !password || password.length > 256) {
      password = ''; $('login-status').textContent = messages.LOGIN_INPUT; loginPassword.focus(); return;
    }
    invalidate(true, true);
    const operation = begin('login'), startedAt = Date.now();
    $('login-status').textContent = '로그인 정보를 확인하고 있습니다…';
    try {
      let request;
      try {
        request = fetch('/auth/login', { method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', cache: 'no-store',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ username, password }), signal: operation.controller.signal });
      } finally { password = ''; }
      const response = await request.catch(error => { if (error?.name === 'AbortError') throw error; throw new SafeError('LOGIN_NETWORK'); });
      if (!current(operation)) { await response.body?.cancel(); return; }
      const result = await readLoginResponse(response);
      operation.controller.signal.throwIfAborted();
      if (!current(operation)) return;
      token = result.mcpAccessToken;
      scheduleExpiry(result.expiresIn, startedAt);
      $('login-status').textContent = '로그인한 사용자로 MCP 연결을 확인하고 있습니다…';
      await connectMcp(operation);
      if (current(operation) && ready) { $('login-status').textContent = ''; closeLogin(); }
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      invalidate(true, true);
      $('connection-note').textContent = failure.message;
      openLogin(failure.message);
      if (failure.code === 'LOGIN_CREDENTIALS') loginPassword.focus();
    } finally {
      password = '';
      if (operation.generation === generation) loginPassword.value = '';
      finish(operation); if (ready && !active && operation.generation === generation) query.focus();
    }
  });
  $('connect-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (active || ready) return;
    const suppliedToken = tokenInput.value.trim();
    if (!suppliedToken || /\s/.test(suppliedToken)) { $('connection-note').textContent = '공백 없는 MCP 토큰을 입력하세요. Bearer 접두사는 제외하세요.'; return; }
    invalidate(true, true); closeLogin(); token = suppliedToken;
    const operation = begin('connect');
    try { await connectMcp(operation); }
    catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      if (failure.code === 'AUTH') requireLogin(failure.message);
      else { invalidate(true, true); $('connection-note').textContent = failure.message; }
    } finally { finish(operation); if (ready && !active && operation.generation === generation) query.focus(); }
  });
  tokenInput.addEventListener('input', () => {
    if (token || ready || active) {
      const sent = active?.sentMutation;
      invalidate(false); closeLogin(); $('connection-note').textContent = `토큰이 변경되어 연결을 해제했습니다. 다시 연결하세요.${sent ? ` ${unknownOutcome} 재연결 후 최신 상태를 조회하세요.` : ''}`;
    }
  });
  $('disconnect').addEventListener('click', () => {
    const sent = active?.sentMutation;
    invalidate(); closeLogin(); $('login-username').value = ''; $('login-status').textContent = '';
    $('connection-note').textContent = `로그아웃하고 토큰과 이전 조회 결과를 지웠습니다.${sent ? ` ${unknownOutcome} 로그인 후 최신 상태를 조회하세요.` : ''}`; $('login-open').focus();
  });
  $('cancel').addEventListener('click', () => { if (active?.kind === 'login') cancelLogin(); else active?.controller.abort(); });
  async function submit(text, focusId = 'query') {
    if (!ready && !active) query.value = text;
    if (!ensureConnected()) return;
    let question;
    try { question = classifyQuestion(text); } catch (error) {
      bubble(error.message, 'assistant', true); $('discovery-status').textContent = error.message;
      $('discovery-status').dataset.state = 'error'; query.focus(); return;
    }
    // Never echo raw input: an accidentally pasted credential must not enter the conversation.
    bubble(question.label, 'user'); query.value = '';
    clearPreview();
    if (question.kind === 'access') {
      $('relationship-view').hidden = true; $('relationship-summary').textContent = '';
      $('relationship-roles').textContent = ''; $('relationship-paths').replaceChildren();
    }
    if (question.kind === 'catalog' || question.kind === 'detail') {
      if (question.kind === 'catalog' || browseCatalog?.entity !== question.entity) {
        browseCatalog = null; options('browse-item', [], '새 응답을 기다리고 있습니다');
      }
      $('catalog-browser').hidden = false; $('browse-result').textContent = '';
      $('browse-status').textContent = '서버에서 허용된 항목을 확인하고 있습니다…';
    }
    const operation = begin();
    let complete = false;
    try {
      const call = (name, args) => rpc(operation, 'tools/call', { name, arguments: args });
      if (question.kind === 'access') {
        const result = await call('get_my_access', {});
        if (!current(operation)) return;
        const graph = accessResult(result);
        renderRelationships(graph, question.view === 'relationships');
        bubble(accessText(graph, question.view));
      } else if (question.kind === 'catalog' || question.kind === 'detail') {
        const args = { entity: question.entity, ...(question.kind === 'detail' ? { id: question.id } : {}) };
        const result = await call(question.kind === 'detail' ? 'get_access_entity' : 'list_access_entities', args);
        if (!current(operation)) return;
        const data = catalogResult(result, question.entity, question.id);
        renderCatalog(data); bubble(catalogText(data));
      } else if (question.kind === 'requestable') {
        const result = await call('get_requestable_roles', {});
        if (!current(operation)) return;
        bubble(requestableText(requestableResult(result)));
      } else if (question.kind === 'concept') {
        const result = await call('explain_authorization', { concept: question.concept });
        if (!current(operation)) return;
        const concept = conceptResult(result, question.concept);
        bubble(`개념 설명 · 실제 권한 근거가 아닙니다\n\n${concept.explanation}\n\n실제 권한은 “내 역할은 뭐야?” 또는 리소스 접근 질문으로 확인하세요.`);
      } else {
        let id = question.resourceId;
        if (question.kind === 'resource-name') {
          const result = await call('get_my_access', {});
          if (!current(operation)) return;
          id = resolveResourceName(accessResult(result), question.name);
        }
        const result = await call('check_my_resource_access', { resourceId: id });
        if (!current(operation)) return;
        const access = resourceResult(result, id);
        bubble(access.allowed
          ? `${access.resource.name} (${id}) · 접근 허용\n${access.resource.description}\n\n서버가 확인한 권한 경로:\n${access.grants.map(grant => `${grant.roleId} → ${grant.policyId} → ${id}`).join('\n')}`
          : `${id} · 접근 허용 안 됨\n현재 사용자에게 허용된 접근 경로가 없습니다. 이 응답으로 리소스의 존재 여부는 알 수 없습니다.`);
      }
      complete = true;
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      bubble(failure.message, 'assistant', true);
      if (failure.code === 'AUTH') requireLogin(failure.message);
      if (question.kind === 'catalog' || question.kind === 'detail') $('browse-status').textContent = failure.message;
      $('discovery-status').textContent = failure.message; $('discovery-status').dataset.state = 'error';
    } finally {
      finish(operation);
      if (ready && !active && operation.generation === generation) $(complete ? focusId : 'discovery-status').focus();
    }
  }
  async function reloadLatest(actorLocalId, mutationState) {
    if (!ready || active) return;
    clearPreview();
    clearDiscovery();
    const operation = begin('refresh');
    operation.sentMutation = mutationState === 'unknown';
    $('manage-status').textContent = '현재 실행자로 최신 권한·카탈로그·요청 가능 역할을 조회합니다.';
    try {
      const raw = await rpc(operation, 'tools/call', { name: 'get_my_access', arguments: {} });
      if (!current(operation)) return;
      const graph = accessResult(raw);
      if (actorLocalId !== undefined && graph.user.id !== actorLocalId) fail();
      const catalogs = [];
      for (const kind of CATALOGS) {
        const result = await rpc(operation, 'tools/call', { name: 'list_access_entities', arguments: { entity: kind } });
        if (!current(operation)) return;
        catalogs.push(catalogResult(result, kind));
      }
      const rawRoles = await rpc(operation, 'tools/call', { name: 'get_requestable_roles', arguments: {} });
      if (!current(operation)) return;
      const roles = requestableResult(rawRoles);
      renderRelationships(graph);
      const chosen = catalogs.find(catalog => catalog.entity === $('browse-entity').value);
      if (chosen) renderCatalog(chosen);
      bubble(accessText(graph, 'resources'));
      for (const catalog of catalogs) bubble(catalogText(catalog));
      bubble(requestableText(roles));
      $('manage-status').textContent = `${mutationState === 'unknown' ? '앞선 변경 결과는 미확인입니다. ' : ''}최신 상태를 조회했습니다. 다른 요청이나 아직 처리 중인 변경으로 이후 상태가 달라질 수 있습니다.`;
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      const note = `${mutationState === 'unknown' ? `${unknownOutcome}\n` : ''}최신 상태를 확인하지 못했습니다. 조회 권한 또는 연결을 확인한 후 다시 조회하세요. ${failure.message}`;
      if (failure.code === 'AUTH') requireLogin(note);
      $('manage-status').textContent = note;
    } finally { finish(operation); }
  }
  const editManagement = () => {
    clearPreview();
    if (active?.kind === 'preview' || active?.kind === 'targets') active.controller.abort();
    $('manage-status').textContent = '작업 또는 대상이 바뀌었습니다. 새 미리보기를 확인하세요.';
    refresh();
  };
  $('manage-operation').addEventListener('change', () => { clearTargets(); editManagement(); });
  for (const id of ['manage-first', 'manage-second']) $(id).addEventListener('input', () => { $(`${id}-select`).value = ''; editManagement(); });
  $('manage-form').addEventListener('submit', async event => {
    event.preventDefault(); // Enter and form submission only prepare; never mutate.
    if (!ready || active) return;
    clearPreview();
    let name, args;
    try {
      name = $('manage-operation').value;
      args = managementArgs(name, $('manage-first').value, $('manage-second').value);
    } catch (error) { $('manage-status').textContent = error.message; refresh(); return; }
    const revision = previewRevision;
    const operation = begin('preview');
    $('manage-status').textContent = '실행자 정보를 조회합니다. 변경 요청은 아직 보내지 않았습니다.';
    try {
      const raw = await rpc(operation, 'tools/call', { name: 'get_my_access', arguments: {} });
      if (!current(operation) || revision !== previewRevision) return;
      const graph = accessResult(raw);
      preview = Object.freeze({ operation: name, validatedArgs: Object.freeze({ ...args }), actorLocalId: graph.user.id,
        actorName: graph.user.name, connectionGeneration: generation, revision });
      $('manage-actor').textContent = `현재 실행자: ${graph.user.name} (${graph.user.id}) · 서버가 확인한 로그인 사용자`;
      $('manage-summary').textContent = `${MANAGEMENT[name].label}\n${Object.entries(args).map(([key, value]) => `${key}: ${value}`).join('\n')}\n해당 관계만 ${MANAGEMENT[name].present ? '추가' : '해제'}합니다. 다른 경로의 유효 권한은 유지될 수 있습니다.`;
      $('manage-preview').hidden = false;
      $('manage-status').textContent = '미리보기를 확인한 뒤 변경 실행을 누르세요. 아직 변경 요청을 보내지 않았습니다.';
      $('manage-preview').setAttribute('tabindex', '-1'); $('manage-preview').focus();
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      const note = `실행자 미리보기를 만들 수 없습니다. 이 UI는 authz.read로 실행자 확인이 필요합니다. ${failure.message}`;
      clearPreview();
      if (failure.code === 'AUTH') requireLogin(note);
      $('manage-status').textContent = note;
    } finally { finish(operation); }
  });
  $('manage-execute').addEventListener('click', async () => {
    if (!ready || active || !preview) return;
    const snapshot = preview;
    const keys = MANAGEMENT[snapshot.operation].keys;
    if (snapshot.connectionGeneration !== generation || snapshot.revision !== previewRevision
        || $('manage-operation').value !== snapshot.operation
        || $('manage-first').value !== snapshot.validatedArgs[keys[0]] || $('manage-second').value !== snapshot.validatedArgs[keys[1]]) {
      editManagement(); return;
    }
    clearPreview(); // Consume once before any await; duplicate clicks cannot send twice.
    clearDiscovery();
    $('messages').replaceChildren();
    const operation = begin('mutation');
    operation.sentMutation = true;
    let outcome = 'unknown';
    $('manage-status').textContent = '변경 요청을 전송합니다. 전송 후 취소는 롤백을 보장하지 않습니다.';
    try {
      const result = await rpc(operation, 'tools/call', { name: snapshot.operation, arguments: snapshot.validatedArgs });
      if (!current(operation)) return;
      const changed = mutationResult(result, snapshot.operation, snapshot.validatedArgs);
      outcome = 'confirmed';
      bubble(`${MANAGEMENT[snapshot.operation].label} · ${changed.changed ? '변경됨' : '이미 같은 상태 · 변경 없음'}\n${Object.entries(changed.relationship).map(([key, value]) => `${key}: ${value}`).join('\n')}\n처리 시점 관계: ${changed.present ? '존재' : '없음'}\n영향 가능 사용자 ID: ${changed.impact.affectedUserIds.join(', ') || '없음'}\n영향 목록은 유효 권한이 반드시 변경됐다는 뜻이 아닙니다. 최신 상태를 다시 조회합니다.`);
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      const note = `${unknownOutcome}\n응답 안내: ${failure.message}`;
      if (failure.code === 'AUTH') {
        requireLogin(`${note} 인증이 필요하여 재조회하지 못했습니다. 로그인 후 최신 상태를 조회하세요.`);
      } else bubble(note, 'assistant', true);
      $('manage-status').textContent = note;
    } finally {
      const mayRead = current(operation) && ready;
      finish(operation);
      if (mayRead) await reloadLatest(snapshot.actorLocalId, outcome);
    }
  });
  $('manage-cancel').addEventListener('click', () => {
    const sent = active?.sentMutation;
    clearPreview();
    if (['preview', 'mutation', 'refresh', 'targets'].includes(active?.kind)) active.controller.abort();
    $('manage-status').textContent = sent ? unknownOutcome : '미리보기 또는 조회를 취소했습니다. 새 변경 요청을 보내지 않았습니다.';
    refresh();
  });
  $('refresh-access').addEventListener('click', () => {
    if (!ready || active) return;
    $('messages').replaceChildren();
    void reloadLatest();
  });
  $('discover-own').addEventListener('click', () => submit('내 권한 관계 보여줘', 'relationship-title'));
  $('discover-requestable').addEventListener('click', () => submit('요청 가능한 역할 알려줘', 'messages'));
  $('discover-concept').addEventListener('click', () => submit('역할과 정책은 어떻게 달라?', 'messages'));
  $('discover-browse').addEventListener('click', () => {
    if (!ensureConnected()) return;
    const kind = $('browse-entity').value;
    if (!CATALOGS.includes(kind)) { $('discovery-status').textContent = '조회할 항목 종류를 선택하세요.'; return; }
    return submit(`${catalogLabels[kind]} 목록`, 'browse-result');
  });
  $('discover-manage').addEventListener('click', () => {
    if (!ensureConnected()) return;
    clearPreview(); $('management-panel').open = true;
    $('manage-status').textContent = '작업을 선택하고 허용된 대상 목록을 불러오거나 정확한 ID를 입력하세요. 아직 변경 요청을 보내지 않았습니다.';
    refresh(); $('manage-operation').focus();
  });
  $('browse-entity').addEventListener('change', () => {
    browseCatalog = null; options('browse-item', [], '이 종류의 목록을 불러오세요');
    $('browse-id').value = ''; $('browse-result').textContent = '';
    $('browse-status').textContent = '종류가 변경되었습니다. 목록 불러오기를 누르세요.';
    clearPreview(); refresh();
  });
  $('browse-item').addEventListener('change', () => {
    if (!ensureConnected()) return;
    const item = browseCatalog?.generation === generation && browseCatalog.entity === $('browse-entity').value
      ? browseCatalog.items.find(candidate => candidate.id === $('browse-item').value) : null;
    $('browse-id').value = item?.id ?? '';
    if (item) $('browse-detail').focus();
  });
  $('browse-detail').addEventListener('click', () => {
    if (!ensureConnected()) return;
    const kind = $('browse-entity').value, id = $('browse-id').value;
    if (!CATALOGS.includes(kind) || !(kind === 'resources' ? resourceId(id) : catalogId(id))) {
      $('discovery-status').textContent = '목록에서 항목을 선택하거나 형식에 맞는 정확한 ID를 입력하세요.';
      $('browse-id').focus(); return;
    }
    return submit(`${catalogLabels[kind]} ${id} 상세`, 'browse-result');
  });
  $('manage-load-targets').addEventListener('click', async () => {
    if (!ensureConnected()) return;
    const name = $('manage-operation').value;
    if (!Object.hasOwn(MANAGEMENT, name)) { $('manage-target-status').textContent = messages.TARGET; return; }
    clearPreview(); clearTargets();
    const operation = begin('targets');
    $('manage-target-status').textContent = '현재 사용자가 조회할 수 있는 대상 목록을 불러옵니다…';
    try {
      const collected = [];
      for (const key of MANAGEMENT[name].keys) {
        const kind = keyCatalog[key];
        const raw = await rpc(operation, 'tools/call', { name: 'list_access_entities', arguments: { entity: kind } });
        if (!current(operation) || $('manage-operation').value !== name) return;
        collected.push(catalogResult(raw, kind).items);
      }
      targetCatalogs = { generation, operation: name, first: collected[0], second: collected[1] };
      options('manage-first-select', collected[0], '첫 번째 대상을 선택하세요', true);
      options('manage-second-select', collected[1], '두 번째 대상을 선택하세요', true);
      const usable = collected.map(items => items.filter(item => !item.protected).length);
      $('manage-target-status').textContent = `선택 가능 대상: ${usable[0]}개 / ${usable[1]}개. ${usable.includes(0) ? '한쪽 목록이 비어 있거나 모두 보호되어 있습니다. ' : ''}목록에 보인다고 변경 권한이 생기지는 않습니다. 실행 시 서버가 다시 검증합니다.`;
    } catch (error) {
      if (!current(operation)) return;
      const failure = safeFailure(error, operation);
      clearTargets();
      if (failure.code === 'AUTH') requireLogin(failure.message);
      $('manage-target-status').textContent = `대상 목록을 불러오지 못했습니다. ${failure.message}`;
      $('discovery-status').textContent = failure.message; $('discovery-status').dataset.state = 'error';
    } finally {
      finish(operation);
      if (ready && operation.generation === generation) $('manage-target-status').focus();
    }
  });
  for (const [side, id] of [['first', 'manage-first-select'], ['second', 'manage-second-select']]) {
    $(id).addEventListener('change', () => {
      if (!ensureConnected()) return;
      const item = targetCatalogs?.generation === generation && targetCatalogs.operation === $('manage-operation').value
        ? targetCatalogs[side].find(candidate => candidate.id === $(id).value) : null;
      clearPreview();
      if (!item || item.protected) {
        $(`manage-${side}`).value = ''; $(id).value = '';
        $('manage-target-status').textContent = '현재 허용 목록의 비보호 항목을 선택하세요. 보호된 관계는 변경할 수 없습니다.';
      } else {
        $(`manage-${side}`).value = item.id;
        $('manage-target-status').textContent = `${item.name} (${item.id}) 선택. 두 대상을 확인하고 변경 미리보기를 누르세요.`;
      }
      refresh();
    });
  }
  $('query-form').addEventListener('submit', event => { event.preventDefault(); void submit(query.value); });
  document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => { void submit(button.dataset.query); }));
  window.addEventListener('pagehide', () => { invalidate(); closeLogin(); $('login-username').value = ''; $('login-status').textContent = ''; });
  refresh();
}
