import type { ReactNode } from "react";

function ActionIcon({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg aria-hidden="true" viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export function SaveIcon() {
  return (
    <ActionIcon>
      <path d="M5 5.5h11l3 3V18.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5z" />
      <path d="M9 5.5v5h6v-5" />
      <path d="M9 18h6" />
    </ActionIcon>
  );
}

export function CopyIcon() {
  return (
    <ActionIcon>
      <rect x="8" y="8" width="10.5" height="10.5" rx="1.5" />
      <path d="M5.5 14.5h-.8A1.2 1.2 0 0 1 3.5 13.3V5.7a1.2 1.2 0 0 1 1.2-1.2h7.6a1.2 1.2 0 0 1 1.2 1.2v.8" />
    </ActionIcon>
  );
}

export function DownloadIcon() {
  return (
    <ActionIcon>
      <path d="M12 4.5v9" />
      <path d="M8.5 10.5L12 14l3.5-3.5" />
      <path d="M5.5 18.5h13" />
    </ActionIcon>
  );
}

export function EditIcon() {
  return (
    <ActionIcon>
      <path d="M4 20l4.5-1 9-9a2.1 2.1 0 1 0-3-3l-9 9z" />
      <path d="M13.5 6.5l4 4" />
    </ActionIcon>
  );
}

export function DeleteIcon() {
  return (
    <ActionIcon>
      <path d="M4.5 7.5h15" />
      <path d="M9.5 4.5h5" />
      <path d="M8.5 7.5v10" />
      <path d="M15.5 7.5v10" />
      <path d="M6.5 7.5l.8 11a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-11" />
    </ActionIcon>
  );
}

export function RefreshIcon() {
  return (
    <ActionIcon>
      <path d="M20 6v5h-5" />
      <path d="M4 18v-5h5" />
      <path d="M6.8 9.2A7 7 0 0 1 18.5 8L20 11" />
      <path d="M17.2 14.8A7 7 0 0 1 5.5 16L4 13" />
    </ActionIcon>
  );
}

export function StartIcon() {
  return (
    <ActionIcon>
      <path d="M9 7.5v9l7-4.5z" fill="currentColor" stroke="none" />
    </ActionIcon>
  );
}

export function StopIcon() {
  return (
    <ActionIcon>
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.5" fill="currentColor" stroke="none" />
    </ActionIcon>
  );
}

export function ExtendIcon() {
  return (
    <ActionIcon>
      <path d="M12 7v5l3 2" />
      <circle cx="12" cy="13" r="7" />
      <path d="M19 5v4" />
      <path d="M17 7h4" />
    </ActionIcon>
  );
}

export function RevokeIcon() {
  return (
    <ActionIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="M8.5 8.5l7 7" />
    </ActionIcon>
  );
}

export function CreateVmIcon() {
  return (
    <ActionIcon>
      <rect x="4.5" y="6.5" width="11" height="8" rx="1.5" />
      <path d="M8 17.5h4" />
      <path d="M18.5 10.5v6" />
      <path d="M15.5 13.5h6" />
    </ActionIcon>
  );
}
