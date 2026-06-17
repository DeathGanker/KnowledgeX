import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const config = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
const mainRs = readFileSync(new URL('../src-tauri/src/main.rs', import.meta.url), 'utf8');
const petJs = readFileSync(new URL('../../web/static/pet/pet.js', import.meta.url), 'utf8');

test('pet window starts hidden and lives behind tray controls', () => {
  const petWindow = config.app.windows.find((windowConfig) => windowConfig.label === 'pet');

  assert.ok(petWindow, 'pet window config should exist');
  assert.equal(petWindow.visible, false);
  assert.match(mainRs, /MenuItem::with_id\(app,\s*"show",\s*"显示投喂提示"/);
  assert.match(mainRs, /MenuItem::with_id\(app,\s*"hide",\s*"隐藏投喂提示"/);
  assert.doesNotMatch(mainRs, /let\s+_tray\s*=\s*tray\.build\(app\)\?/);
  assert.match(mainRs, /app\.manage\(tray\.build\(app\)\?\)/);
  assert.match(mainRs, /\.title\("KX"\)/);
  assert.match(mainRs, /\.icon_as_template\(false\)/);
});

test('clipboard monitor is enabled by default for tray-first capture', () => {
  assert.match(mainRs, /enabled:\s*AtomicBool::new\(true\)/);
  assert.match(mainRs, /MenuItem::with_id\(app,\s*"watch_on",\s*"开启复制监听"/);
  assert.match(mainRs, /MenuItem::with_id\(app,\s*"watch_off",\s*"关闭复制监听"/);
});

test('copy proposal hides itself when the user ignores or dismisses it', () => {
  assert.match(mainRs, /struct PetPrompt/);
  assert.match(mainRs, /fn schedule_pet_auto_hide\(app: &AppHandle\)/);
  assert.match(mainRs, /Duration::from_secs\(12\)/);
  assert.match(mainRs, /schedule_pet_auto_hide\(app\)/);
  assert.match(mainRs, /fn hide_pet\(app: AppHandle\)/);
  assert.match(mainRs, /fn hold_pet_prompt/);
  assert.match(mainRs, /fn release_pet_prompt/);
  assert.match(mainRs, /hide_pet,/);
  assert.match(mainRs, /hold_pet_prompt,/);
  assert.match(mainRs, /release_pet_prompt,/);
  assert.match(petJs, /AUTO_HIDE_ON_IDLE\s*=\s*true/);
  assert.match(petJs, /hidePetWindow/);
  assert.match(petJs, /holdPetPrompt/);
  assert.match(petJs, /releasePetPrompt/);
  assert.match(petJs, /t\.core\.invoke\("hide_pet"\)/);
  assert.match(petJs, /dismissCard\(\);\s*hidePetWindow\(\)/);
  assert.match(petJs, /if \(AUTO_HIDE_ON_IDLE\) hidePetWindow\(\)/);
});
