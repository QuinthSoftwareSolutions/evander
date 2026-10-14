export type Binding = {response_pointer: string; encoding: "json" | "string"};
export type Profile = {
  enabled: boolean; browser_origin: string; login_url: string;
  adapter?: string; adapter_config?: Record<string, unknown>; cookies_response_pointer?: string | null;
  credential_source: "stored" | "environment"; credential_ref?: string | null;
  credential_fields: Record<string, string>; public_credential_fields: string[];
  static_fields: Record<string, unknown>; local_storage: Record<string, Binding>; session_storage: Record<string, Binding>;
  jwt_response_pointer: string | null; expires_in_response_pointer: string | null;
  max_age_seconds: number; renew_before_seconds: number; timeout_seconds: number; max_response_bytes: number;
};
export type Entry = {id: string; profile: Profile; local: boolean;
  credentials: {configured_fields: string[]; managed_by?: string; updated_at: number | null; owner_directory: string | null};
  last_test: {status: string; checked_at: number; expires_at: number | null} | null};
export type Snapshot = {directory_path: string; revision: string; profiles: Entry[]};
export type Credential = {name: string; reference: string; value: string; public: boolean};
export type Storage = {name: string; location: "local" | "session"; pointer: string; encoding: "json" | "string"};
export type Draft = {id: string; existing: boolean; profile: Profile; credentials: Credential[]; storage: Storage[]; staticJson: string};

export function makeDraft(entry?: Entry): Draft {
  const profile: Profile = entry ? structuredClone(entry.profile) : {
    enabled: true, browser_origin: "", login_url: "", credential_source: "stored",
    credential_fields: {username: "username", password: "password"}, public_credential_fields: ["username"],
    static_fields: {}, local_storage: {accessToken: {response_pointer: "/token", encoding: "string"}}, session_storage: {},
    jwt_response_pointer: null, expires_in_response_pointer: null,
    max_age_seconds: 3600, renew_before_seconds: 60, timeout_seconds: 20, max_response_bytes: 262144,
  };
  return {id: entry?.id ?? "", existing: Boolean(entry), profile,
    credentials: Object.entries(profile.credential_fields).map(([name, reference]) => ({name, reference, value: "", public: profile.public_credential_fields.includes(name)})),
    storage: (["local", "session"] as const).flatMap(location => Object.entries(location === "local" ? profile.local_storage : profile.session_storage)
      .map(([name, binding]) => ({name, location, pointer: binding.response_pointer, encoding: binding.encoding}))),
    staticJson: JSON.stringify(profile.static_fields, null, 2)};
}
