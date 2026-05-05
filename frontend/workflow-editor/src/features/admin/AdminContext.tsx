import { createContext, useContext } from "react";

export type DirectoryInfo = {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
  path: string;
};

export type DirectoryTreeNode = {
  directory: DirectoryInfo;
  config_domains: string[];
  children: DirectoryTreeNode[];
};

export type AdminResult<T> = {
  ok: boolean;
  data: T | null;
  errors: Array<{ code: string; message: string; field?: string | null }>;
  validation_diagnostics: Array<{ code: string; message: string; field?: string | null }>;
};

export type AdminUiState = {
  privilegedUnlocked: boolean;
  authBusy: boolean;
  directoryScopedConfigEnabled: boolean;
  currentUserId: string;
  currentUserLabel: string;
  currentUserRole: "admin" | "regular" | null;
  canManageUsers: boolean;
  selectedDirectoryPath: string;
  selectedDirectoryParentPath: string | null;
  directoryTree: AdminResult<DirectoryTreeNode> | null;
  directoryStateLoading: boolean;
  directoryStateError: string | null;
  directoryPickerOpen: boolean;
  openDirectoryPicker: () => void;
  closeDirectoryPicker: () => void;
  setSelectedDirectoryPath: (path: string) => void;
  refreshDirectoryState: () => Promise<void>;
  refreshAdminState: () => Promise<void>;
};

const DEFAULT_ADMIN_UI_STATE: AdminUiState = {
  privilegedUnlocked: false,
  authBusy: false,
  directoryScopedConfigEnabled: false,
  currentUserId: "anonymous",
  currentUserLabel: "anonymous",
  currentUserRole: null,
  canManageUsers: false,
  selectedDirectoryPath: "root",
  selectedDirectoryParentPath: null,
  directoryTree: null,
  directoryStateLoading: false,
  directoryStateError: null,
  directoryPickerOpen: false,
  openDirectoryPicker: () => {},
  closeDirectoryPicker: () => {},
  setSelectedDirectoryPath: () => {},
  refreshDirectoryState: async () => {},
  refreshAdminState: async () => {},
};

export const AdminUiContext = createContext<AdminUiState>(DEFAULT_ADMIN_UI_STATE);

export function useAdminUi(): AdminUiState {
  return useContext(AdminUiContext);
}
