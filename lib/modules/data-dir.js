/** Single data-directory contract for dsh-biocore-ui.
 *
 * Both files the plugin owns live under the same directory:
 *   <dataDir>/sessions/<session_id>.json   (per-session BioCore state)
 *   <dataDir>/flags.json                   (four switches)
 *
 * Resolution order:
 *   1. --profile <name> / --profile=<name> from process.argv
 *      -> <homedir>/.dsh/profiles/<name>/data, only when that profile dir exists
 *   2. config.dataDir (cordis insert config)
 *   3. BIOCORE_UI_DATA_DIR
 *   4. <DSH_PROFILE_DIR>/data -- only when it is a real DSH profile directory
 *      (contains package.json or dsh.yml)
 *   5. <home>/.dsh/profiles/web/data -- when profiles/web/package.json exists
 *   6. <home>/.dsh/profiles/<only>/data -- exactly one profile under profiles/
 *   7. <plugin>/data -- last resort, logs __bioDataDirFallback
 *      (never cwd; previous node_modules data is migrated, not deleted)
 *
 * Why argv first: DSH passes the serving process its own --profile flag, but
 * DSH_PROFILE_DIR in the environment can point at the *default* profile (web)
 * even when the instance runs --profile web-bio. Trusting the env first wrote
 * session state into the wrong profile's data dir.
 */
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

export const DATA_DIR_ENV = 'BIOCORE_UI_DATA_DIR'
export const PROFILE_DIR_ENV = 'DSH_PROFILE_DIR'
export const PROFILE_MARKERS = Object.freeze(['package.json', 'dsh.yml'])
/** Fallback: the plugin checkout's own data/ directory. */
export const PLUGIN_DATA_DIR = fileURLToPath(new URL('../../data', import.meta.url))
export const FALLBACK_WARN_PREFIX = '__bioDataDirFallback'
export const MIGRATE_LOG_PREFIX = '__bioDataMigrated'

let warnedFallback = false

function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

/** First profile name from argv: --profile X, --profile=X or ''. */
export function profileFromArgv(argv) {
  const list = Array.isArray(argv) ? argv : []
  for (let index = 0; index < list.length; index += 1) {
    const arg = String(list[index] == null ? '' : list[index])
    if (arg === '--profile') {
      const next = text(list[index + 1] == null ? '' : list[index + 1])
      if (next && !next.startsWith('-')) return next
      continue
    }
    const match = /^--profile=(.+)$/.exec(arg)
    if (match) {
      const name = text(match[1])
      if (name) return name
    }
  }
  return ''
}

/** <home>/.dsh/profiles/<name>/data */
export function profileDataDir(name, homeDir) {
  const home = text(homeDir) || homedir()
  return join(resolve(home), '.dsh', 'profiles', String(name), 'data')
}

/** True only when dir looks like a DSH profile root (has a manifest marker). */
export function isProfileDir(dir) {
  const path = text(dir)
  if (!path) return false
  try {
    return PROFILE_MARKERS.some((name) => existsSync(join(path, name)))
  } catch {
    return false
  }
}

/** <home>/.dsh/profiles */
export function profilesRootDir(homeDir) {
  const home = text(homeDir) || homedir()
  return join(resolve(home), '.dsh', 'profiles')
}

/** All real profile names directly under <home>/.dsh/profiles. */
export function listProfiles(homeDir) {
  const root = profilesRootDir(homeDir)
  let names = []
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry && entry.isDirectory())
      .map((entry) => entry.name)
  } catch { return [] }
  return names.filter((name) => isProfileDir(join(root, name)))
}

function warnPluginFallback(path) {
  if (warnedFallback) return
  warnedFallback = true
  try {
    console.error(FALLBACK_WARN_PREFIX + ' using plugin data dir=' + path
      + ' reason=no-profile-dir (writes will live inside the plugin package)')
  } catch { /* logging must not throw */ }
}

/** Copy existing legacy plugin data into the resolved profile dataDir.
 * Missing files are copied; existing files are never overwritten; the old
 * plugin copy stays in place for rollback. */
export function migrateLegacyPluginData(dataDir, legacyDir) {
  const target = text(dataDir)
  const source = text(legacyDir) || PLUGIN_DATA_DIR
  const result = { migrated: false, copied: 0, target, source }
  if (!target || resolve(target) === resolve(source)) return result
  // Only migrate into <profile>/data when the parent is a real profile root.
  const parent = dirname(resolve(target))
  if (!isProfileDir(parent)) return result
  const copyIfMissing = (from, to) => {
    try {
      if (!existsSync(from) || existsSync(to)) return false
      mkdirSync(dirname(to), { recursive: true })
      copyFileSync(from, to)
      result.copied += 1
      return true
    } catch { return false }
  }
  const files = ['flags.json', 'persona_profile.json', 'persona.md', 'config.json']
  for (const name of files) copyIfMissing(join(source, name), join(target, name))
  try {
    const sessions = readdirSync(join(source, 'sessions'))
    for (const name of sessions) {
      copyIfMissing(join(source, 'sessions', name), join(target, 'sessions', name))
    }
  } catch { /* no legacy sessions */ }
  result.migrated = result.copied > 0
  if (result.migrated) {
    try {
      console.error(MIGRATE_LOG_PREFIX + ' from=' + source + ' to=' + target
        + ' copied=' + result.copied)
    } catch { /* logging must not throw */ }
  }
  return result
}

/**
 * Resolve the one directory that owns flags.json and sessions/.
 * @param {object} [config] plugin config (config.dataDir)
 * @param {object} [env] environment map (default process.env; injectable)
 * @param {string[]} [argv] process argv (default process.argv; injectable)
 * @param {string} [homeDir] home directory (default os.homedir(); injectable)
 */
export function resolveDataDir(config, env, argv, homeDir) {
  const cfg = config && typeof config === 'object' ? config : {}
  const environment = env && typeof env === 'object' ? env : process.env
  const args = Array.isArray(argv) ? argv : process.argv

  // 1) --profile is authoritative for the running instance.
  const profile = profileFromArgv(args)
  if (profile) {
    const dataDir = profileDataDir(profile, homeDir)
    if (existsSync(dirname(dataDir))) return dataDir
  }

  // 2) explicit plugin config.
  const fromConfig = text(cfg.dataDir)
  if (fromConfig) return resolve(fromConfig)

  // 3) explicit environment override.
  const fromEnv = text(environment[DATA_DIR_ENV])
  if (fromEnv) return resolve(fromEnv)

  // 4) profile env, only when it really points at a profile root.
  const profileDir = text(environment[PROFILE_DIR_ENV])
  if (profileDir) {
    const candidate = resolve(profileDir)
    if (isProfileDir(candidate)) return join(candidate, 'data')
  }

  // 5) default web profile, when it is a real profile.
  const webProfile = join(profilesRootDir(homeDir), 'web')
  if (isProfileDir(webProfile)) return join(webProfile, 'data')

  // 6) exactly one profile -> deterministic; multiple profiles are ambiguous.
  const profiles = listProfiles(homeDir)
  if (profiles.length === 1) {
    return join(profilesRootDir(homeDir), profiles[0], 'data')
  }

  // 7) last resort: the plugin package itself.
  const fallback = resolve(PLUGIN_DATA_DIR)
  warnPluginFallback(fallback)
  return fallback
}