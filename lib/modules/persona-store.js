/** Shared persona storage across DSH profiles.
 *
 * Canonical location: <home>/.dsh/biocore/{persona.md,persona_profile.json}
 * Read order: shared -> this profile's dataDir copy.
 * Writes always refresh both; first startup migrates an existing profile copy
 * into the shared directory.
 */
import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const SHARED_PERSONA_ENV = 'BIOCORE_SHARED_PERSONA_DIR'

export function sharedPersonaDir() {
  const override = String(process.env[SHARED_PERSONA_ENV] || '').trim()
  return override || join(homedir(), '.dsh', 'biocore')
}

export function sharedPersonaPaths() {
  const dir = sharedPersonaDir()
  return { dir, profile: join(dir, 'persona_profile.json'), text: join(dir, 'persona.md') }
}

/** This profile's own copy (kept for compatibility / rollback). */
export function profilePersonaPaths(dataDir) {
  const dir = String(dataDir || '').trim()
  return { dir, profile: join(dir, 'persona_profile.json'), text: join(dir, 'persona.md') }
}

function hasAny(paths) {
  try { return existsSync(paths.profile) || existsSync(paths.text) } catch { return false }
}

/**
 * Resolve the persona files actually used: shared wins when present.
 * Returns { source:'shared'|'profile', profile, text }.
 */
export function resolvePersonaPaths(dataDir) {
  const shared = sharedPersonaPaths()
  if (hasAny(shared)) return Object.assign({ source: 'shared' }, shared)
  return Object.assign({ source: 'profile' }, profilePersonaPaths(dataDir))
}

/** Copy the currently-resolved persona into the shared directory. */
export function syncPersonaToShared(dataDir) {
  const shared = sharedPersonaPaths()
  const profile = profilePersonaPaths(dataDir)
  const copied = []
  try {
    if (existsSync(profile.profile)) {
      mkdirSync(shared.dir, { recursive: true })
      copyFileSync(profile.profile, shared.profile)
      copied.push('profile')
    }
    if (existsSync(profile.text)) {
      mkdirSync(shared.dir, { recursive: true })
      copyFileSync(profile.text, shared.text)
      copied.push('text')
    }
  } catch { /* best-effort: profile copy still works */ }
  return { copied, shared }
}

/** Copy the resolved (shared or profile) persona back into this profile. */
export function syncPersonaToProfile(dataDir) {
  const source = resolvePersonaPaths(dataDir)
  const profile = profilePersonaPaths(dataDir)
  const copied = []
  try {
    if (existsSync(source.profile) && source.profile !== profile.profile) {
      mkdirSync(profile.dir, { recursive: true })
      copyFileSync(source.profile, profile.profile)
      copied.push('profile')
    }
    if (existsSync(source.text) && source.text !== profile.text) {
      mkdirSync(profile.dir, { recursive: true })
      copyFileSync(source.text, profile.text)
      copied.push('text')
    }
  } catch { /* best-effort */ }
  return { copied, source }
}

/**
 * First-start migration: when the shared directory has no persona yet but this
 * profile does, copy it over. Safe to call on every startup.
 */
export function migratePersonaToShared(dataDir) {
  const shared = sharedPersonaPaths()
  if (hasAny(shared)) return { migrated: false, reason: 'shared_exists', source: 'shared' }
  const profile = profilePersonaPaths(dataDir)
  if (!existsSync(profile.profile) && !existsSync(profile.text)) {
    return { migrated: false, reason: 'profile_empty', source: 'profile' }
  }
  const result = syncPersonaToShared(dataDir)
  return { migrated: true, copied: result.copied, source: 'shared' }
}
