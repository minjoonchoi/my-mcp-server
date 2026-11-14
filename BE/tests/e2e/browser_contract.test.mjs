import assert from 'node:assert/strict';
import test from 'node:test';
import { claimsRollbackCompleted, grantPathTuple } from './browser_contract.mjs';

test('unknown-outcome warnings do not falsely claim rollback', () => {
  for (const text of ['결과 미확인. 취소·연결 해제는 롤백이 아닙니다.', '결과를 확인할 수 없습니다. 롤백되지 않았을 수 있습니다.', '롤백 완료 여부는 알 수 없습니다.', '서버 상태를 다시 확인하세요.']) {
    assert.equal(claimsRollbackCompleted(text), false, text);
  }
});
test('positive rollback and no-change certainty are rejected', () => {
  for (const text of ['롤백 완료', '변경을 롤백했습니다.', '롤백되었습니다.', '변경을 되돌렸습니다.', '변경되지 않았습니다.']) {
    assert.equal(claimsRollbackCompleted(text), true, text);
  }
});

test('grant paths extract exactly one user/role/policy/resource tuple', () => {
  const row = '사용자 앨리스 (alice) → 역할 분석가 (role-analyst) → 정책 분석 조회 (policy-reports) → 리소스 보고서 (R100)';
  assert.deepEqual(grantPathTuple(row), ['alice', 'role-analyst', 'policy-reports', 'R100']);
  assert.throws(() => grantPathTuple(row + ' → 역할 편집자 (role-editor)'));
  assert.throws(() => grantPathTuple(row + ' R300 policy-shared role-editor'));
  const second = row.replace('policy-reports', 'policy-shared');
  assert.notDeepEqual([row, row].map(grantPathTuple).sort(), [row, second].map(grantPathTuple).sort());
});
