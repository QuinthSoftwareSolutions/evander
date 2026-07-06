import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import companyLogoDark from "../branding/qss-dark.png";
import companyLogoLight from "../branding/qss-light.png";
import logoDark from "../branding/evander-logo-dark.png";
import logoLight from "../branding/evander-logo-light.png";
import WorkflowEditorApp from "./App";
import {
  EMPTY_ADMIN_BRANDING,
  initialAdminBranding,
  normalizeAdminBranding,
  themedLogoUrl,
  type AdminBrandingConfig,
} from "./adminBranding";
import { fetchJson } from "./features/admin/api";
import { AdminUiContext } from "./features/admin/AdminContext";
import type { AdminResult, DirectoryTreeNode } from "./features/admin/AdminContext";
import { useDocumentVisible } from "./features/admin/visibility";
import { AddonsPage } from "./features/addons/AddonsPage";
import { AgentsPage } from "./features/agents/AgentsPage";
import { ArtifactsPage } from "./features/artifacts/ArtifactsPage";
import { ConfigurationScreen } from "./features/configuration/ConfigurationScreen";
import { IssuesPage } from "./features/issues/IssuesPage";
import { ApprovalsScreen } from "./features/approvals/ApprovalsScreen";
import { OperationsDashboard } from "./features/dashboard/OperationsDashboard";
import { McpToolsPage } from "./features/mcpTools/McpToolsPage";
import { ReportsPage } from "./features/reports/ReportsPage";
import { SessionsScreen } from "./features/sessions/SessionsScreen";
import { ToolVisibilityPage } from "./features/toolVisibility/ToolVisibilityPage";
import { ToolCallsPage } from "./features/toolCalls/ToolCallsPage";
import { SkillsPage } from "./features/skills/SkillsPage";
import { UsersPage } from "./features/users/UsersPage";
import { VmsPage } from "./features/vms/VmsPage";
import { WorkersScreen } from "./features/workers/WorkersScreen";
import { ChatLauncher } from "./features/chat/ChatLauncher";
import { DirectoryAddIcon, DirectoryDeleteIcon, NotificationBellIcon } from "./features/admin/PageActionIcons";
import { Tooltip } from "./features/admin/Tooltip";
import {
  addonTabsFromPayload,
  currentTab,
  nextPathFromHash,
  tabsForExecutionBackend,
  TABS,
  type AdminAddonNavigationPayload,
  type AdminTab,
  type AdminTabDefinition,
} from "./adminRouting";

type AdminSession = {
  auth_mode: "none" | "local_password" | "azure_sso";
  auth_required: boolean;
  authenticated: boolean;
  username: string | null;
  display_name: string | null;
  role: "admin" | "regular" | null;
  can_manage_users: boolean;
  permissions?: string[];
  effective_permissions?: string[];
  session_id: string | null;
  available_login_methods: string[];
  azure_sso_login_url: string | null;
  privileged_operations: {
    unlocked: boolean;
  };
  branding?: unknown;
};

type AdminState = {
  selected_directory_path: string;
  directory_scoped_config_enabled: boolean;
  execution_backend: AdminExecutionBackend;
  role: "admin" | "regular" | null;
  can_manage_users: boolean;
  permissions?: string[];
  effective_permissions?: string[];
  privileged_operations: {
    unlocked: boolean;
  };
  assistant?: {
    enabled: boolean;
    show_launcher_when_disabled: boolean;
  };
  addons?: AdminAddonNavigationPayload[];
  deployment?: DeploymentStatus;
};

type PrivilegedOperationsResponse = {
  message?: string;
  privileged_operations?: {
    unlocked?: boolean;
  };
};

type DeploymentStatus = {
  active: boolean;
  message: string | null;
  source: string | null;
  started_at: string | null;
  updated_at: string | null;
  ttl_seconds: number;
  stale: boolean;
};

type UserNotification = {
  id: string;
  title: string;
  body: string;
  source: string;
  source_id: string;
  target_url: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
};

type UserNotificationState = {
  unread_count: number;
  notifications: UserNotification[];
};

type UserNotificationResponse = UserNotificationState & {
  user_id: string;
  marked_notification_ids?: string[];
};

type DirectoryStateResponse = {
  directory_scoped_config_enabled: boolean;
  selected_directory_path: string;
  execution_backend: AdminExecutionBackend;
  selected_directory: {
    path: string;
    parent_path: string | null;
    can_create: boolean;
    can_delete: boolean;
  };
  tree: AdminResult<DirectoryTreeNode>;
};

type DirectoryMutationResult = AdminResult<unknown>;

type AdminExecutionBackend = "libvirt_vm" | "container_worker_vm";

const WORKFLOW_READ_PERMISSIONS = new Set(["workflows.read", "workflows.write"]);
const WORKFLOW_WRITE_PERMISSION = "workflows.write";
const WORKFLOW_PACKS_MANAGE_PERMISSION = "workflow_packs.manage";
const WORKFLOW_RUN_READ_PERMISSION = "workflow_runs.read";
const WORKFLOW_RUN_CONTROL_PERMISSION = "workflow_runs.control";
const USER_MANAGEMENT_PERMISSIONS = new Set(["users.manage"]);

function normalizePermissionList(...values: Array<ReadonlyArray<string> | string[] | undefined>): string[] {
  const permissions = new Set<string>();
  for (const value of values) {
    for (const permission of value ?? []) {
      const normalized = permission.trim();
      if (normalized) {
        permissions.add(normalized);
      }
    }
  }
  return [...permissions].sort((left, right) => left.localeCompare(right));
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const WORKFLOW_SIDEBAR_STORAGE_KEY = "admin-web:workflow:sections-collapsed";
const DIRECTORY_STORAGE_KEY = "admin-web:selected-directory";
const PRIVILEGE_UNLOCKED_STORAGE_KEY = "admin-web:privileges-unlocked";
const THEME_STORAGE_KEY = "admin-web:theme";
const BOOTSTRAPPED_ADMIN_BRANDING = initialAdminBranding();

const LOGIN_BACKGROUND_NODES = [
  { x: 0.08, y: 0.2, radius: 3.1, hue: "#7ce8ff", phase: 0.2 },
  { x: 0.26, y: 0.26, radius: 4.3, hue: "#f4ce7a", phase: 1.1 },
  { x: 0.43, y: 0.19, radius: 3.4, hue: "#91ffc7", phase: 2.2 },
  { x: 0.63, y: 0.27, radius: 4.5, hue: "#7ce8ff", phase: 0.8 },
  { x: 0.82, y: 0.2, radius: 3.3, hue: "#91ffc7", phase: 1.9 },
  { x: 0.16, y: 0.54, radius: 2.9, hue: "#91ffc7", phase: 0.5 },
  { x: 0.34, y: 0.47, radius: 4.2, hue: "#7ce8ff", phase: 1.7 },
  { x: 0.56, y: 0.58, radius: 3.6, hue: "#f4ce7a", phase: 2.8 },
  { x: 0.76, y: 0.49, radius: 4.4, hue: "#91ffc7", phase: 3.2 },
  { x: 0.92, y: 0.56, radius: 3.1, hue: "#7ce8ff", phase: 1.4 },
  { x: 0.12, y: 0.82, radius: 2.8, hue: "#7ce8ff", phase: 2.6 },
  { x: 0.31, y: 0.73, radius: 4.1, hue: "#91ffc7", phase: 0.9 },
  { x: 0.5, y: 0.81, radius: 3.5, hue: "#f4ce7a", phase: 1.8 },
  { x: 0.7, y: 0.72, radius: 4.2, hue: "#7ce8ff", phase: 2.9 },
  { x: 0.9, y: 0.8, radius: 3.2, hue: "#91ffc7", phase: 0.3 },
] as const;

const LOGIN_BACKGROUND_EDGES = [
  { from: 0, to: 1, icons: ["mail", "chat"], speed: 0.00023, phase: 0.03 },
  { from: 1, to: 2, icons: ["review", "agent"], speed: 0.00022, phase: 0.16 },
  { from: 2, to: 3, icons: ["code", "script"], speed: 0.00025, phase: 0.29 },
  { from: 3, to: 4, icons: ["pr", "git"], speed: 0.00024, phase: 0.42 },
  { from: 5, to: 6, icons: ["chat", "mail"], speed: 0.00021, phase: 0.11 },
  { from: 6, to: 7, icons: ["agent", "vm"], speed: 0.00023, phase: 0.31 },
  { from: 7, to: 8, icons: ["code", "review"], speed: 0.00022, phase: 0.53 },
  { from: 8, to: 9, icons: ["pr", "git"], speed: 0.00024, phase: 0.71 },
  { from: 10, to: 11, icons: ["mail", "script"], speed: 0.0002, phase: 0.09 },
  { from: 11, to: 12, icons: ["vm", "agent"], speed: 0.00021, phase: 0.28 },
  { from: 12, to: 13, icons: ["code", "review"], speed: 0.00023, phase: 0.47 },
  { from: 13, to: 14, icons: ["git", "pr"], speed: 0.00022, phase: 0.66 },
  { from: 1, to: 6, icons: ["mail", "chat"], speed: 0.00015, phase: 0.22 },
  { from: 7, to: 13, icons: ["agent", "vm"], speed: 0.00014, phase: 0.58 },
] as const;

type AdminTheme = "light" | "dark";

function LoginBackgroundCanvas({ theme }: { theme: AdminTheme }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvasElement = canvasRef.current;
    if (!canvasElement) return;
    const contextValue = canvasElement.getContext("2d");
    if (!contextValue) return;
    const canvas = canvasElement;
    const context = contextValue;
    const palette =
      theme === "dark"
        ? {
            edge: "rgba(140, 205, 236, 0.14)",
            rippleCore: "rgba(124, 232, 255, 0.85)",
            rippleMid: "rgba(145, 255, 199, 0.3)",
            cursorCore: 0.08,
            cursorMid: 0.04,
            bubbleFill: "rgba(7, 18, 28, 0.78)",
            bubbleInset: "rgba(255, 255, 255, 0.04)",
          }
        : {
            edge: "rgba(48, 109, 142, 0.28)",
            rippleCore: "rgba(37, 125, 175, 0.92)",
            rippleMid: "rgba(62, 171, 130, 0.46)",
            cursorCore: 0.15,
            cursorMid: 0.08,
            bubbleFill: "rgba(12, 28, 40, 0.86)",
            bubbleInset: "rgba(255, 255, 255, 0.06)",
          };

    let frame = 0;
    let iconSerial = 0;
    const pointer = {
      x: -10_000,
      y: -10_000,
      targetX: -10_000,
      targetY: -10_000,
      renderedX: -10_000,
      renderedY: -10_000,
      active: false,
      targetInfluence: 0,
      influence: 0,
      lastMoveAt: 0,
      lastFrameAt: 0,
    };
    const iconBursts: Array<{
      x: number;
      y: number;
      glyph: string;
      tint: string;
      bornAt: number;
      lifetime: number;
      offset: number;
    }> = [];
    const iconCooldowns = new Map<number, number>();

    function drawRoundedRect(x: number, y: number, width: number, height: number, radius: number) {
      const r = Math.min(radius, width / 2, height / 2);
      context.beginPath();
      context.moveTo(x + r, y);
      context.lineTo(x + width - r, y);
      context.quadraticCurveTo(x + width, y, x + width, y + r);
      context.lineTo(x + width, y + height - r);
      context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
      context.lineTo(x + r, y + height);
      context.quadraticCurveTo(x, y + height, x, y + height - r);
      context.lineTo(x, y + r);
      context.quadraticCurveTo(x, y, x + r, y);
      context.closePath();
    }

    function iconForType(type: string): { glyph: string; tint: string } {
      if (type === "mail") return { glyph: "mail", tint: "#7ce8ff" };
      if (type === "chat") return { glyph: "chat", tint: "#91ffc7" };
      if (type === "git") return { glyph: "branch", tint: "#f4ce7a" };
      if (type === "pr") return { glyph: "merge", tint: "#7ce8ff" };
      if (type === "review") return { glyph: "check", tint: "#91ffc7" };
      if (type === "vm") return { glyph: "screen", tint: "#f4ce7a" };
      if (type === "code") return { glyph: "code", tint: "#7ce8ff" };
      if (type === "script") return { glyph: "terminal", tint: "#91ffc7" };
      if (type === "agent") return { glyph: "spark", tint: "#f4ce7a" };
      return { glyph: "spark", tint: "#7ce8ff" };
    }

    function drawGlyph(glyph: string, centerX: number, centerY: number, size: number, tint: string) {
      const s = size;
      context.save();
      context.strokeStyle = tint;
      context.fillStyle = tint;
      context.lineWidth = 1.4;
      context.lineCap = "round";
      context.lineJoin = "round";

      if (glyph === "mail") {
        drawRoundedRect(centerX - s * 0.7, centerY - s * 0.45, s * 1.4, s * 0.9, 3);
        context.stroke();
        context.beginPath();
        context.moveTo(centerX - s * 0.68, centerY - s * 0.38);
        context.lineTo(centerX, centerY + s * 0.02);
        context.lineTo(centerX + s * 0.68, centerY - s * 0.38);
        context.stroke();
      } else if (glyph === "chat") {
        drawRoundedRect(centerX - s * 0.68, centerY - s * 0.48, s * 1.28, s * 0.9, 4);
        context.stroke();
        context.beginPath();
        context.moveTo(centerX - s * 0.18, centerY + s * 0.42);
        context.lineTo(centerX - s * 0.02, centerY + s * 0.68);
        context.lineTo(centerX + s * 0.12, centerY + s * 0.38);
        context.stroke();
      } else if (glyph === "branch") {
        context.beginPath();
        context.arc(centerX - s * 0.46, centerY - s * 0.46, s * 0.18, 0, Math.PI * 2);
        context.arc(centerX + s * 0.46, centerY - s * 0.12, s * 0.18, 0, Math.PI * 2);
        context.arc(centerX - s * 0.12, centerY + s * 0.46, s * 0.18, 0, Math.PI * 2);
        context.fill();
        context.beginPath();
        context.moveTo(centerX - s * 0.28, centerY - s * 0.38);
        context.lineTo(centerX + s * 0.28, centerY - s * 0.16);
        context.lineTo(centerX - s * 0.02, centerY + s * 0.28);
        context.stroke();
      } else if (glyph === "merge") {
        context.beginPath();
        context.moveTo(centerX - s * 0.5, centerY - s * 0.4);
        context.lineTo(centerX - s * 0.12, centerY - s * 0.4);
        context.lineTo(centerX + s * 0.18, centerY - s * 0.06);
        context.lineTo(centerX + s * 0.5, centerY - s * 0.06);
        context.moveTo(centerX - s * 0.5, centerY + s * 0.4);
        context.lineTo(centerX - s * 0.12, centerY + s * 0.4);
        context.lineTo(centerX + s * 0.18, centerY + s * 0.06);
        context.lineTo(centerX + s * 0.5, centerY + s * 0.06);
        context.stroke();
      } else if (glyph === "check") {
        context.beginPath();
        context.moveTo(centerX - s * 0.45, centerY + s * 0.02);
        context.lineTo(centerX - s * 0.1, centerY + s * 0.34);
        context.lineTo(centerX + s * 0.5, centerY - s * 0.34);
        context.stroke();
      } else if (glyph === "screen") {
        drawRoundedRect(centerX - s * 0.72, centerY - s * 0.52, s * 1.44, s * 0.94, 3);
        context.stroke();
        context.beginPath();
        context.moveTo(centerX - s * 0.18, centerY + s * 0.56);
        context.lineTo(centerX + s * 0.18, centerY + s * 0.56);
        context.moveTo(centerX, centerY + s * 0.42);
        context.lineTo(centerX, centerY + s * 0.56);
        context.stroke();
      } else if (glyph === "code") {
        context.beginPath();
        context.moveTo(centerX - s * 0.46, centerY);
        context.lineTo(centerX - s * 0.12, centerY - s * 0.3);
        context.moveTo(centerX - s * 0.46, centerY);
        context.lineTo(centerX - s * 0.12, centerY + s * 0.3);
        context.moveTo(centerX + s * 0.46, centerY);
        context.lineTo(centerX + s * 0.12, centerY - s * 0.3);
        context.moveTo(centerX + s * 0.46, centerY);
        context.lineTo(centerX + s * 0.12, centerY + s * 0.3);
        context.moveTo(centerX + s * 0.04, centerY - s * 0.38);
        context.lineTo(centerX - s * 0.04, centerY + s * 0.38);
        context.stroke();
      } else if (glyph === "terminal") {
        drawRoundedRect(centerX - s * 0.72, centerY - s * 0.5, s * 1.44, s * 1, 3);
        context.stroke();
        context.beginPath();
        context.moveTo(centerX - s * 0.42, centerY - s * 0.1);
        context.lineTo(centerX - s * 0.18, centerY + s * 0.12);
        context.lineTo(centerX - s * 0.42, centerY + s * 0.34);
        context.moveTo(centerX - s * 0.02, centerY + s * 0.34);
        context.lineTo(centerX + s * 0.34, centerY + s * 0.34);
        context.stroke();
      } else {
        context.beginPath();
        context.arc(centerX, centerY, s * 0.16, 0, Math.PI * 2);
        context.fill();
        context.beginPath();
        context.moveTo(centerX, centerY - s * 0.52);
        context.lineTo(centerX, centerY + s * 0.52);
        context.moveTo(centerX - s * 0.52, centerY);
        context.lineTo(centerX + s * 0.52, centerY);
        context.stroke();
      }

      context.restore();
    }

    function drawIconBubble(x: number, y: number, glyph: string, tint: string, opacity: number, offset: number) {
      const drawX = x + offset - 13;
      const drawY = y - 33;

      context.save();
      context.globalAlpha = opacity;
      context.shadowBlur = 14;
      context.shadowColor = tint;
      context.fillStyle = palette.bubbleFill;
      context.strokeStyle = tint;
      context.lineWidth = 1.1;
      drawRoundedRect(drawX, drawY, 26, 26, 8);
      context.fill();
      context.stroke();
      context.shadowBlur = 0;
      context.fillStyle = palette.bubbleInset;
      drawRoundedRect(drawX + 1.2, drawY + 1.2, 23.6, 23.6, 7);
      context.fill();
      drawGlyph(glyph, drawX + 13, drawY + 13, 9.5, tint);
      context.restore();
    }

    function spawnIconBurst(x: number, y: number, type: string, time: number) {
      const { glyph, tint } = iconForType(type);
      const offset = (iconSerial % 2 === 0 ? -1 : 1) * (12 + (iconSerial % 3) * 6);
      iconSerial += 1;
      iconBursts.push({
        x,
        y,
        glyph,
        tint,
        bornAt: time,
        lifetime: 1500,
        offset,
      });
    }

    function drawIconBursts(time: number) {
      for (let index = iconBursts.length - 1; index >= 0; index -= 1) {
        const burst = iconBursts[index];
        const age = time - burst.bornAt;
        if (age >= burst.lifetime) {
          iconBursts.splice(index, 1);
          continue;
        }
        const progress = age / burst.lifetime;
        const rise = progress * 20;
        const opacity = (1 - progress) * 0.78;
        drawIconBubble(burst.x, burst.y - rise, burst.glyph, burst.tint, opacity, burst.offset);
      }
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const width = window.innerWidth;
      const height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function cursorInfluence(x: number, y: number, radius: number) {
      if (pointer.influence <= 0.001) return { strength: 0, offsetX: 0, offsetY: 0 };
      const dx = x - pointer.renderedX;
      const dy = y - pointer.renderedY;
      const distance = Math.hypot(dx, dy) || 1;
      if (distance >= radius) return { strength: 0, offsetX: 0, offsetY: 0 };
      const strength = ((radius - distance) / radius) ** 2 * pointer.influence;
      const push = strength * 18;
      return {
        strength,
        offsetX: (dx / distance) * push,
        offsetY: (dy / distance) * push,
      };
    }

    function interactiveNodePosition(node: (typeof LOGIN_BACKGROUND_NODES)[number], width: number, height: number) {
      const baseX = node.x * width;
      const baseY = node.y * height;
      const influence = cursorInfluence(baseX, baseY, 180);
      return {
        x: baseX + influence.offsetX,
        y: baseY + influence.offsetY,
        strength: influence.strength,
      };
    }

    function drawNode(x: number, y: number, radius: number, color: string, glow: number) {
      context.save();
      context.shadowBlur = 18 + glow * 10;
      context.shadowColor = color;
      context.fillStyle = color;
      context.globalAlpha = 0.95;
      context.beginPath();
      context.arc(x, y, radius + glow, 0, Math.PI * 2);
      context.fill();
      context.globalAlpha = 0.16;
      context.beginPath();
      context.arc(x, y, radius * 4 + glow * 6, 0, Math.PI * 2);
      context.fill();
      context.restore();
    }

    function draw(time: number) {
      const width = window.innerWidth;
      const height = window.innerHeight;
      context.clearRect(0, 0, width, height);
      iconSerial = 0;
      const deltaMs = pointer.lastFrameAt > 0 ? Math.min(48, time - pointer.lastFrameAt) : 16;
      pointer.lastFrameAt = time;
      const settleDecay = Math.exp(-deltaMs / 420);
      pointer.targetInfluence *= settleDecay;
      const followFactor = 1 - Math.exp(-deltaMs / 140);
      pointer.renderedX += (pointer.targetX - pointer.renderedX) * followFactor;
      pointer.renderedY += (pointer.targetY - pointer.renderedY) * followFactor;
      const influenceFactor = 1 - Math.exp(-deltaMs / 220);
      pointer.influence += (pointer.targetInfluence - pointer.influence) * influenceFactor;
      if (pointer.influence < 0.003) {
        pointer.influence = 0;
      }
      const positionedNodes = LOGIN_BACKGROUND_NODES.map((node) => interactiveNodePosition(node, width, height));

      LOGIN_BACKGROUND_EDGES.forEach((edge, edgeIndex) => {
        const start = positionedNodes[edge.from];
        const end = positionedNodes[edge.to];
        const startX = start.x;
        const startY = start.y;
        const endX = end.x;
        const endY = end.y;
        const midX = (startX + endX) / 2;
        const midY = (startY + endY) / 2;
        const edgeInfluence = cursorInfluence(midX, midY, 220);
        const normalX = endY - startY;
        const normalY = -(endX - startX);
        const normalLength = Math.hypot(normalX, normalY) || 1;
        const curveAmount = edgeInfluence.strength * 24;
        const controlX = midX + (normalX / normalLength) * curveAmount;
        const controlY = midY + (normalY / normalLength) * curveAmount;

        context.save();
        context.strokeStyle =
          theme === "dark"
            ? `rgba(140, 205, 236, ${0.14 + edgeInfluence.strength * 0.22})`
            : `rgba(48, 109, 142, ${0.28 + edgeInfluence.strength * 0.26})`;
        context.lineWidth = (theme === "dark" ? 1.2 : 1.45) + edgeInfluence.strength * 1.15;
        context.beginPath();
        context.moveTo(startX, startY);
        context.quadraticCurveTo(controlX, controlY, endX, endY);
        context.stroke();

        const progress = ((time * edge.speed + edge.phase) % 1 + 1) % 1;
        const inverse = 1 - progress;
        const pulseX = inverse * inverse * startX + 2 * inverse * progress * controlX + progress * progress * endX;
        const pulseY = inverse * inverse * startY + 2 * inverse * progress * controlY + progress * progress * endY;
        const gradient = context.createRadialGradient(pulseX, pulseY, 0, pulseX, pulseY, 18);
        gradient.addColorStop(0, palette.rippleCore);
        gradient.addColorStop(0.5, palette.rippleMid);
        gradient.addColorStop(1, theme === "dark" ? "rgba(124, 232, 255, 0)" : "rgba(37, 125, 175, 0)");
        context.fillStyle = gradient;
        context.beginPath();
        context.arc(pulseX, pulseY, 18, 0, Math.PI * 2);
        context.fill();
        context.restore();
        const distanceToEnd = Math.hypot(endX - pulseX, endY - pulseY);
        const lastBurstAt = iconCooldowns.get(edgeIndex) ?? -Infinity;
        if (distanceToEnd < 22 && time - lastBurstAt > 850) {
          const iconType = edge.icons[Math.floor((time * 0.001 + edge.phase) % edge.icons.length)];
          spawnIconBurst(endX, endY, iconType, time);
          iconCooldowns.set(edgeIndex, time);
        }
      });

      drawIconBursts(time);

      LOGIN_BACKGROUND_NODES.forEach((node, index) => {
        const position = positionedNodes[index];
        const glow = 0.8 + Math.sin(time * 0.0012 + node.phase) * 0.4 + position.strength * 0.65;
        const color =
          theme === "dark"
            ? node.hue
            : node.hue === "#7ce8ff"
            ? "#236f9f"
            : node.hue === "#91ffc7"
            ? "#2e8d72"
            : "#b27b22";
        drawNode(position.x, position.y, node.radius, color, glow);
      });

      if (pointer.influence > 0) {
        const rippleRadius = 100 + pointer.influence * 40;
        const ripple = context.createRadialGradient(pointer.renderedX, pointer.renderedY, 0, pointer.renderedX, pointer.renderedY, rippleRadius);
        ripple.addColorStop(
          0,
          theme === "dark"
            ? `rgba(124, 232, 255, ${palette.cursorCore * pointer.influence})`
            : `rgba(37, 125, 175, ${palette.cursorCore * pointer.influence})`,
        );
        ripple.addColorStop(
          0.4,
          theme === "dark"
            ? `rgba(124, 232, 255, ${palette.cursorMid * pointer.influence})`
            : `rgba(62, 171, 130, ${palette.cursorMid * pointer.influence})`,
        );
        ripple.addColorStop(1, theme === "dark" ? "rgba(124, 232, 255, 0)" : "rgba(37, 125, 175, 0)");
        context.fillStyle = ripple;
        context.beginPath();
        context.arc(pointer.renderedX, pointer.renderedY, rippleRadius, 0, Math.PI * 2);
        context.fill();
      }

      frame = window.requestAnimationFrame(draw);
    }

    function handlePointerMove(event: PointerEvent) {
      const now = performance.now();
      const previousX = pointer.targetX;
      const previousY = pointer.targetY;
      if (pointer.lastMoveAt > 0) {
        const elapsed = Math.max(8, now - pointer.lastMoveAt);
        const distance = Math.hypot(event.clientX - previousX, event.clientY - previousY);
        const instantaneousVelocity = distance / elapsed;
        const velocityInfluence = Math.min(1, instantaneousVelocity / 1.15);
        pointer.targetInfluence = Math.max(pointer.targetInfluence * 0.72, velocityInfluence);
      } else {
        pointer.renderedX = event.clientX;
        pointer.renderedY = event.clientY;
      }
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      pointer.targetX = event.clientX;
      pointer.targetY = event.clientY;
      pointer.lastMoveAt = now;
      pointer.active = true;
    }

    function handlePointerLeave() {
      pointer.active = false;
      pointer.targetInfluence *= 0.92;
    }

    resize();
    frame = window.requestAnimationFrame(draw);
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerleave", handlePointerLeave);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerleave", handlePointerLeave);
    };
  }, [theme]);

  return <canvas ref={canvasRef} className="admin-login-flow-canvas" aria-hidden="true" />;
}

function writeStoredPrivilegeUnlocked(unlocked: boolean) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(PRIVILEGE_UNLOCKED_STORAGE_KEY, String(unlocked));
  } catch {
    // Best-effort UI preference persistence.
  }
}

function getInitialTheme(): AdminTheme {
  if (typeof window === "undefined") return "light";
  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (storedTheme === "light" || storedTheme === "dark") {
    return storedTheme;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function PlaceholderView({ title, description }: { title: string; description: string }) {
  return (
    <div className="admin-placeholder">
      <div className="admin-placeholder-card">
        <div className="eyebrow">In Progress</div>
        <h2>{title}</h2>
        <p>{description}</p>
        <p>This area is wired into the new admin shell and waiting for its dedicated feature module.</p>
      </div>
    </div>
  );
}

function addonUiPathWithDirectory(
  uiPath: string,
  selectedDirectoryPath: string,
  privilegedUnlocked: boolean,
): string {
  const directoryPath = selectedDirectoryPath.trim() || "root";
  try {
    const base = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const url = new URL(uiPath, base);
    url.searchParams.set("directory_path", directoryPath);
    url.searchParams.set("privileged_unlocked", privilegedUnlocked ? "true" : "false");
    if (/^[a-z][a-z0-9+.-]*:/i.test(uiPath)) {
      return url.toString();
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    const [withoutHash, hash = ""] = uiPath.split("#", 2);
    const separator = withoutHash.includes("?") ? "&" : "?";
    return `${withoutHash}${separator}directory_path=${encodeURIComponent(directoryPath)}&privileged_unlocked=${privilegedUnlocked ? "true" : "false"}${hash ? `#${hash}` : ""}`;
  }
}

function AddonView({
  tab,
  selectedDirectoryPath,
  privilegedUnlocked,
}: {
  tab: AdminTabDefinition;
  selectedDirectoryPath: string;
  privilegedUnlocked: boolean;
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const iframeSrcKey = `${tab.uiPath ?? ""}\n${selectedDirectoryPath}`;
  const iframeSrcRef = useRef<{ key: string; value: string } | null>(null);
  if (iframeSrcRef.current?.key !== iframeSrcKey) {
    iframeSrcRef.current = {
      key: iframeSrcKey,
      value: addonUiPathWithDirectory(tab.uiPath ?? "", selectedDirectoryPath, privilegedUnlocked),
    };
  }
  const iframeSrc = iframeSrcRef.current.value;

  function postPrivilegeState() {
    iframeRef.current?.contentWindow?.postMessage(
      {
        type: "admin-privileged-operations",
        privileged_operations: { unlocked: privilegedUnlocked },
      },
      window.location.origin,
    );
  }

  useEffect(() => {
    postPrivilegeState();
  }, [privilegedUnlocked, iframeSrc]);

  if (tab.uiPath) {
    return (
      <iframe
        ref={iframeRef}
        className="admin-addon-frame"
        title={tab.label}
        src={iframeSrc}
        onLoad={postPrivilegeState}
        sandbox="allow-forms allow-modals allow-same-origin allow-scripts"
      />
    );
  }
  return <PlaceholderView title={tab.label} description={tab.description} />;
}

function formatDeploymentStartedAt(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function DeploymentStatusBanner({ status }: { status: DeploymentStatus | null }) {
  if (!status?.active) {
    return null;
  }
  const startedAt = formatDeploymentStartedAt(status.started_at ?? status.updated_at);
  return (
    <div className="admin-deployment-banner" role="status" aria-live="polite">
      <div className="admin-deployment-banner-icon" aria-hidden="true">
        !
      </div>
      <div className="admin-deployment-banner-copy">
        <strong>{status.message || "Control plane update in progress."}</strong>
        {startedAt ? <span>Started {startedAt}</span> : null}
      </div>
    </div>
  );
}

function expandedDirectoryAncestors(path: string): string[] {
  const normalized = path.trim() || "root";
  if (normalized === "root") {
    return ["root"];
  }
  const segments = normalized.split("/").filter(Boolean);
  const ancestors = ["root"];
  let current = "";
  for (const segment of segments) {
    current = current ? `${current}/${segment}` : segment;
    ancestors.push(current);
  }
  return ancestors;
}

function findDirectoryNode(root: DirectoryTreeNode | null, path: string): DirectoryTreeNode | null {
  if (!root) return null;
  if (root.directory.path === path) return root;
  for (const child of root.children) {
    const match = findDirectoryNode(child, path);
    if (match) {
      return match;
    }
  }
  return null;
}

function formatNotificationTimestamp(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AdminUserNotifications({
  open,
  busy,
  error,
  state,
  onToggle,
  onClose,
  onMarkAllRead,
  onNotificationClick,
}: {
  open: boolean;
  busy: boolean;
  error: string | null;
  state: UserNotificationState;
  onToggle: () => void;
  onClose: () => void;
  onMarkAllRead: () => void;
  onNotificationClick: (notification: UserNotification) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const unreadCount = state.unread_count;
  const notifications = state.notifications;

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        onClose();
      }
    }
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeydown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeydown);
    };
  }, [onClose, open]);

  return (
    <div className="admin-notification-menu-wrap" ref={containerRef}>
      <Tooltip content="Unread notifications.">
        <button
          type="button"
          className={`admin-notification-button${unreadCount > 0 ? " has-unread" : ""}`}
          onClick={onToggle}
          aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : "No unread notifications"}
          aria-expanded={open}
          aria-haspopup="menu"
        >
          <NotificationBellIcon />
          {unreadCount > 0 ? <span className="admin-notification-badge">{unreadCount > 99 ? "99+" : unreadCount}</span> : null}
        </button>
      </Tooltip>
      {open ? (
        <div className="admin-notification-menu" role="menu">
          <div className="admin-notification-menu-header">
            <strong>Notifications</strong>
            <button type="button" onClick={onMarkAllRead} disabled={busy || unreadCount === 0}>
              Clear
            </button>
          </div>
          {error ? <div className="admin-notification-error">{error}</div> : null}
          {notifications.length ? (
            <div className="admin-notification-list">
              {notifications.map((notification) => (
                <button
                  key={notification.id}
                  type="button"
                  className="admin-notification-item"
                  onClick={() => onNotificationClick(notification)}
                  role="menuitem"
                >
                  <span className="admin-notification-item-title">{notification.title}</span>
                  {notification.body ? <span className="admin-notification-item-body">{notification.body}</span> : null}
                  <time>{formatNotificationTimestamp(notification.created_at)}</time>
                </button>
              ))}
            </div>
          ) : (
            <div className="admin-notification-empty">{busy ? "Loading." : "No unread notifications."}</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function DirectoryTreeBranch({
  node,
  selectedPath,
  expandedPaths,
  onToggle,
  canCreate,
  canDelete,
  mutationBusy,
  onRequestCreate,
  onRequestDelete,
  onSelect,
}: {
  node: DirectoryTreeNode;
  selectedPath: string;
  expandedPaths: Record<string, boolean>;
  onToggle: (path: string) => void;
  canCreate: boolean;
  canDelete: boolean;
  mutationBusy: boolean;
  onRequestCreate: (path: string) => void;
  onRequestDelete: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expandedPaths[node.directory.path] ?? false;
  const canDeleteNode = canDelete && node.directory.path !== "root" && !hasChildren;
  return (
    <li>
      <div className="directory-tree-row">
        {hasChildren ? (
          <button
            type="button"
            className="directory-tree-toggle"
            aria-label={isExpanded ? `Collapse ${node.directory.path}` : `Expand ${node.directory.path}`}
            aria-expanded={isExpanded}
            onClick={() => onToggle(node.directory.path)}
          >
            <span aria-hidden="true">{isExpanded ? "▾" : "▸"}</span>
          </button>
        ) : (
          <span className="directory-tree-spacer" aria-hidden="true" />
        )}
        <button
          type="button"
          className={`directory-tree-button${node.directory.path === selectedPath ? " selected" : ""}`}
          onClick={() => onSelect(node.directory.path)}
        >
          <span className="directory-tree-label">{node.directory.path}</span>
        </button>
        <div className="directory-tree-actions">
          {canCreate ? (
            <Tooltip content={`Create a child directory under ${node.directory.path}.`}>
              <button
                type="button"
                className="directory-tree-action-button"
                aria-label={`Create child directory under ${node.directory.path}`}
                onClick={() => onRequestCreate(node.directory.path)}
                disabled={mutationBusy}
              >
                <DirectoryAddIcon />
              </button>
            </Tooltip>
          ) : null}
          {canDeleteNode ? (
            <Tooltip content={`Delete ${node.directory.path}. Only empty directories can be removed.`}>
              <button
                type="button"
                className="directory-tree-action-button danger"
                aria-label={`Delete ${node.directory.path}`}
                onClick={() => onRequestDelete(node.directory.path)}
                disabled={mutationBusy}
              >
                <DirectoryDeleteIcon />
              </button>
            </Tooltip>
          ) : null}
        </div>
      </div>
      {hasChildren && isExpanded ? (
        <ul className="directory-tree-list">
          {node.children.map((child) => (
            <DirectoryTreeBranch
              key={child.directory.path}
              node={child}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              onToggle={onToggle}
              canCreate={canCreate}
              canDelete={canDelete}
              mutationBusy={mutationBusy}
              onRequestCreate={onRequestCreate}
              onRequestDelete={onRequestDelete}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function DirectoryChooserModal({
  open,
  loading,
  error,
  selectedPath,
  privilegedUnlocked,
  directoryScopedConfigEnabled,
  tree,
  onClose,
  onRefreshState,
  onSelect,
}: {
  open: boolean;
  loading: boolean;
  error: string | null;
  selectedPath: string;
  privilegedUnlocked: boolean;
  directoryScopedConfigEnabled: boolean;
  tree: AdminResult<DirectoryTreeNode> | null;
  onClose: () => void;
  onRefreshState: (path?: string) => Promise<void>;
  onSelect: (path: string) => void;
}) {
  const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
  const [createParentPath, setCreateParentPath] = useState<string | null>(null);
  const [newDirectoryName, setNewDirectoryName] = useState("");
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutationStatus, setMutationStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setExpandedPaths((current) => {
      const next = { ...current };
      for (const path of expandedDirectoryAncestors(selectedPath)) {
        next[path] = true;
      }
      return next;
    });
  }, [open, selectedPath]);

  if (!open) return null;
  const root = tree?.ok ? tree.data : null;
  const canCreate = directoryScopedConfigEnabled && privilegedUnlocked;
  const canDelete = directoryScopedConfigEnabled && privilegedUnlocked;

  async function handleCreateDirectory() {
    if (!createParentPath) {
      return;
    }
    const name = newDirectoryName.trim();
    if (!name) {
      setMutationError("Directory name is required.");
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    setMutationStatus(null);
    try {
      const result = await fetchJson<DirectoryMutationResult>("/api/configuration/directories", {
        method: "POST",
        body: JSON.stringify({ parent_path: createParentPath, name }),
      });
      if (!result.ok) {
        throw new Error(result.errors[0]?.message ?? "Directory create failed.");
      }
      const createdPath = `${createParentPath}/${name}`;
      setNewDirectoryName("");
      setCreateParentPath(null);
      await onRefreshState(createdPath);
      setExpandedPaths((current) => {
        const next = { ...current };
        for (const path of expandedDirectoryAncestors(createdPath)) {
          next[path] = true;
        }
        return next;
      });
      setMutationStatus(`Created ${createdPath}.`);
    } catch (reason) {
      setMutationError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMutationBusy(false);
    }
  }

  async function handleDeleteDirectory(path: string) {
    const node = findDirectoryNode(root, path);
    if (!canDelete || !node || path === "root" || node.children.length > 0) {
      return;
    }
    if (
      !window.confirm(
        `Delete ${path}?\n\nDeletion is only allowed for non-root directories with no child directories and no VMs with sessions.`,
      )
    ) {
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    setMutationStatus(null);
    try {
      const result = await fetchJson<DirectoryMutationResult>(
        `/api/configuration/directories?directory_path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      );
      if (!result.ok) {
        throw new Error(result.errors[0]?.message ?? "Directory delete failed.");
      }
      const deletedPath = path;
      const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "root";
      await onRefreshState(selectedPath === path ? parentPath || "root" : selectedPath);
      setMutationStatus(`Deleted ${deletedPath}.`);
    } catch (reason) {
      setMutationError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMutationBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card directory-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="directory-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <div className="eyebrow">Directory chooser</div>
            <h2 id="directory-modal-title">Choose directory</h2>
            <p>Select the shared directory context for directory-scoped admin views.</p>
          </div>
          <div className="inline-actions">
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="modal-body">
          <div className="directory-modal-summary">
            <span className="section-label">Current directory</span>
            <strong>{selectedPath}</strong>
          </div>
          {mutationStatus ? <div className="callout success-callout">{mutationStatus}</div> : null}
          {mutationError ? <div className="callout error-callout">{mutationError}</div> : null}
          {error ? <div className="callout error-callout">{error}</div> : null}
          {loading && !root ? <div className="empty-state">Loading directories…</div> : null}
          {!loading && tree && !tree.ok ? <div className="empty-state">The directory tree could not be loaded.</div> : null}
          {root ? (
            <ul className="directory-tree-list root">
              <DirectoryTreeBranch
                node={root}
                selectedPath={selectedPath}
                expandedPaths={expandedPaths}
                onToggle={(path) => setExpandedPaths((current) => ({ ...current, [path]: !(current[path] ?? false) }))}
                canCreate={canCreate}
                canDelete={canDelete}
                mutationBusy={loading || mutationBusy}
                onRequestCreate={(path) => {
                  setCreateParentPath(path);
                  setNewDirectoryName("");
                  setMutationError(null);
                }}
                onRequestDelete={(path) => void handleDeleteDirectory(path)}
                onSelect={onSelect}
              />
            </ul>
          ) : null}
        </div>
      </div>
      {createParentPath ? (
        <div className="modal-backdrop nested-modal-backdrop" onClick={() => setCreateParentPath(null)} role="presentation">
          <div className="modal-card directory-create-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <div className="eyebrow">Directory</div>
                <h2>Create child directory</h2>
                <p>Create a child under {createParentPath}.</p>
              </div>
            </div>
            <div className="modal-body">
              <div className="field-stack">
                <input
                  type="text"
                  value={newDirectoryName}
                  placeholder="New child directory"
                  onChange={(event) => setNewDirectoryName(event.target.value)}
                  disabled={mutationBusy}
                  autoFocus
                />
              </div>
              <div className="inline-actions">
                <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={() => setCreateParentPath(null)} disabled={mutationBusy}>
                  Cancel
                </button>
                <button type="button" className="admin-shell-button" onClick={() => void handleCreateDirectory()} disabled={mutationBusy}>
                  {mutationBusy ? "Working..." : "Create"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LoginBrandBar({
  theme,
  branding,
  brandingLoaded,
  onToggleTheme,
}: {
  theme: AdminTheme;
  branding: AdminBrandingConfig;
  brandingLoaded: boolean;
  onToggleTheme: () => void;
}) {
  const companyLogoUrl = brandingLoaded
    ? themedLogoUrl(branding.company_logo, theme, companyLogoLight, companyLogoDark)
    : null;
  const productLogoUrl = brandingLoaded
    ? themedLogoUrl(branding.product_logo, theme, logoLight, logoDark)
    : null;
  return (
    <div className="admin-login-brandbar">
      {companyLogoUrl ? (
        <img className="admin-login-company-logo" src={companyLogoUrl} alt={branding.company_logo.alt} />
      ) : (
        <span className="admin-login-company-logo admin-login-logo-placeholder" aria-hidden="true" />
      )}
      <button type="button" className="admin-shell-button admin-shell-button-muted admin-login-theme-toggle" onClick={onToggleTheme}>
        {theme === "dark" ? "Light mode" : "Dark mode"}
      </button>
      {productLogoUrl ? (
        <img className="admin-login-truman-logo" src={productLogoUrl} alt={branding.product_logo.alt} />
      ) : (
        <span className="admin-login-truman-logo admin-login-logo-placeholder" aria-hidden="true" />
      )}
    </div>
  );
}

function LoginScreen({
  session,
  theme,
  branding,
  brandingLoaded,
  onToggleTheme,
  deploymentStatus,
  error,
  loading,
  onSubmit,
  onAzureSignIn,
}: {
  session: AdminSession;
  theme: AdminTheme;
  branding: AdminBrandingConfig;
  brandingLoaded: boolean;
  onToggleTheme: () => void;
  deploymentStatus: DeploymentStatus | null;
  error: string | null;
  loading: boolean;
  onSubmit: (username: string, password: string) => Promise<void>;
  onAzureSignIn: () => void;
}) {
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    await onSubmit(String(formData.get("username") ?? ""), String(formData.get("password") ?? ""));
  }

  return (
    <div className="admin-login-shell">
      <div className="admin-login-background" aria-hidden="true">
        <LoginBackgroundCanvas theme={theme} />
        <div className="admin-login-noise" />
        <div className="admin-login-aurora admin-login-aurora-left" />
        <div className="admin-login-aurora admin-login-aurora-right" />
      </div>
      <div className="admin-login-stage">
        <LoginBrandBar
          theme={theme}
          branding={branding}
          brandingLoaded={brandingLoaded}
          onToggleTheme={onToggleTheme}
        />
        <DeploymentStatusBanner status={deploymentStatus} />
        <div className="admin-login-card">
          <h1>Admin Sign In</h1>
          <p>
            {session.auth_mode === "azure_sso"
              ? "Authenticate with Microsoft Entra to access the multi-user admin surface."
              : "Authenticate to access the multi-user admin surface."}
          </p>
          {session.auth_mode === "local_password" ? (
            <form className="admin-login-form" onSubmit={handleSubmit}>
              <label className="inspector-label">
                <span>Username</span>
                <input name="username" defaultValue="admin" autoComplete="username" />
              </label>
              <label className="inspector-label">
                <span>Password</span>
                <input name="password" type="password" autoComplete="current-password" />
              </label>
              {error ? <div className="admin-login-error">{error}</div> : null}
              <button className="admin-shell-button admin-login-action" type="submit" disabled={loading}>
                {loading ? "Signing in..." : "Sign in"}
              </button>
            </form>
          ) : null}
          {session.auth_mode === "azure_sso" ? (
            <div className="admin-login-actions">
              {error ? <div className="admin-login-error">{error}</div> : null}
              <button
                className="admin-shell-button admin-login-action"
                type="button"
                disabled={loading || !session.azure_sso_login_url}
                onClick={onAzureSignIn}
              >
                {loading ? "Redirecting..." : "Sign in with Microsoft"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function AdminRoot() {
  const [theme, setTheme] = useState<AdminTheme>(() => getInitialTheme());
  const [branding, setBranding] = useState<AdminBrandingConfig>(
    () => BOOTSTRAPPED_ADMIN_BRANDING ?? EMPTY_ADMIN_BRANDING,
  );
  const [brandingLoaded, setBrandingLoaded] = useState(
    () => BOOTSTRAPPED_ADMIN_BRANDING !== null,
  );
  const [session, setSession] = useState<AdminSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [privilegeUnlocked, setPrivilegeUnlocked] = useState(false);
  const [selectedTab, setSelectedTab] = useState<AdminTab>(() => currentTab(window.location.hash));
  const [workflowHasUnsavedChanges, setWorkflowHasUnsavedChanges] = useState(false);
  const [adminState, setAdminState] = useState<AdminState | null>(null);
  const [deploymentStatus, setDeploymentStatus] = useState<DeploymentStatus | null>(null);
  const [selectedDirectoryPath, setSelectedDirectoryPath] = useState<string>(() => {
    if (typeof window === "undefined") return "root";
    return window.localStorage.getItem(DIRECTORY_STORAGE_KEY) || "root";
  });
  const [selectedDirectoryParentPath, setSelectedDirectoryParentPath] = useState<string | null>(null);
  const [directoryTree, setDirectoryTree] = useState<AdminResult<DirectoryTreeNode> | null>(null);
  const [directoryStateLoading, setDirectoryStateLoading] = useState(false);
  const [directoryStateError, setDirectoryStateError] = useState<string | null>(null);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);
  const [userNotifications, setUserNotifications] = useState<UserNotificationState>({
    unread_count: 0,
    notifications: [],
  });
  const [userNotificationsOpen, setUserNotificationsOpen] = useState(false);
  const [userNotificationsBusy, setUserNotificationsBusy] = useState(false);
  const [userNotificationsError, setUserNotificationsError] = useState<string | null>(null);
  const [workflowSidebarCollapsed, setWorkflowSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(WORKFLOW_SIDEBAR_STORAGE_KEY) === "true";
  });
  const suppressNextHashChangeRef = useRef(false);
  const adminExecutionBackend = adminState?.execution_backend ?? null;
  const addonTabs = useMemo(() => addonTabsFromPayload(adminState?.addons), [adminState?.addons]);
  const visibleTabs = useMemo(
    () => tabsForExecutionBackend(adminExecutionBackend, addonTabs),
    [adminExecutionBackend, addonTabs],
  );
  const documentVisible = useDocumentVisible();
  const renderedTab = visibleTabs.some((tab) => tab.key === selectedTab)
    ? selectedTab
    : visibleTabs[0]?.key ?? "workflow";
  const activeTab = visibleTabs.find((tab) => tab.key === renderedTab) ?? TABS[TABS.length - 1];
  const shellBackgroundPollingEnabled = documentVisible && !selectedTab.startsWith("addon:");

  function applyPrivilegeLockState(unlocked: boolean) {
    setPrivilegeUnlocked(unlocked);
    writeStoredPrivilegeUnlocked(unlocked);
  }

  useEffect(() => {
    let cancelled = false;
    async function loadBranding() {
      try {
        const payload = await fetchJson<unknown>("/api/admin/branding");
        if (!cancelled) {
          setBranding(normalizeAdminBranding(payload));
          setBrandingLoaded(true);
        }
      } catch {
        // Keep the bundled defaults, or any branding already received with the session payload.
      }
    }
    void loadBranding();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.title = branding.page_title;
  }, [branding.page_title]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (storedTheme === "light" || storedTheme === "dark") {
      return;
    }
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (event: MediaQueryListEvent) => {
      setTheme(event.matches ? "dark" : "light");
    };
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadSession() {
      try {
        const payload = await fetchJson<AdminSession>("/api/auth/session", { credentials: "same-origin" });
        if (!cancelled) {
          if (payload.branding) {
            setBranding(normalizeAdminBranding(payload.branding));
            setBrandingLoaded(true);
          }
          setSession(payload);
          applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
          setSessionError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setSessionError(error instanceof Error ? error.message : "Unable to load session.");
        }
      }
    }
    void loadSession();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!shellBackgroundPollingEnabled) {
      return;
    }
    let cancelled = false;
    async function loadDeploymentStatus() {
      try {
        const payload = await fetchJson<DeploymentStatus>("/api/admin/deployment-status");
        if (!cancelled) {
          setDeploymentStatus(payload);
        }
      } catch {
        if (!cancelled) {
          setDeploymentStatus((current) => (current?.active ? current : null));
        }
      }
    }
    void loadDeploymentStatus();
    const intervalId = window.setInterval(() => {
      void loadDeploymentStatus();
    }, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [shellBackgroundPollingEnabled]);

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      return;
    }
    let cancelled = false;
    async function loadAdminState() {
      try {
        const payload = await fetchJson<AdminState>(
          `/api/admin/state?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
          { credentials: "same-origin" },
        );
        if (!cancelled) {
          setAdminState(payload);
          applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
          if (payload.deployment) {
            setDeploymentStatus(payload.deployment);
          }
          if (!window.localStorage.getItem(DIRECTORY_STORAGE_KEY)) {
            setSelectedDirectoryPath(payload.selected_directory_path || "root");
          }
        }
      } catch (error) {
        if (!cancelled) {
          setSessionError(error instanceof Error ? error.message : "Unable to load admin state.");
        }
      }
    }
    void loadAdminState();
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (adminExecutionBackend === null) {
      return;
    }
    if (visibleTabs.some((tab) => tab.key === selectedTab)) {
      return;
    }
    const fallbackTab = visibleTabs[0]?.key ?? "workflow";
    suppressNextHashChangeRef.current = true;
    window.location.hash = fallbackTab;
    setSelectedTab(fallbackTab);
  }, [adminExecutionBackend, selectedTab, visibleTabs]);

  useEffect(() => {
    window.localStorage.setItem(WORKFLOW_SIDEBAR_STORAGE_KEY, String(workflowSidebarCollapsed));
  }, [workflowSidebarCollapsed]);

  useEffect(() => {
    window.localStorage.setItem(DIRECTORY_STORAGE_KEY, selectedDirectoryPath);
  }, [selectedDirectoryPath]);

  useEffect(() => {
    if (!directoryPickerOpen) {
      return;
    }
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setDirectoryPickerOpen(false);
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [directoryPickerOpen]);

  async function loadDirectoryState(targetPath = selectedDirectoryPath) {
    setDirectoryStateLoading(true);
    try {
      const payload = await fetchJson<DirectoryStateResponse>(`/api/configuration/state?directory_path=${encodeURIComponent(targetPath)}`);
      setSelectedDirectoryPath(payload.selected_directory_path);
      setSelectedDirectoryParentPath(payload.selected_directory.parent_path);
      setDirectoryTree(payload.tree);
      setAdminState((current) =>
        current
          ? {
              ...current,
              selected_directory_path: payload.selected_directory_path,
              directory_scoped_config_enabled: payload.directory_scoped_config_enabled,
              execution_backend: payload.execution_backend,
            }
          : current,
      );
      setDirectoryStateError(null);
    } catch (error) {
      setDirectoryStateError(error instanceof Error ? error.message : "Unable to load directory state.");
    } finally {
      setDirectoryStateLoading(false);
    }
  }

  async function refreshAdminState() {
    try {
      const payload = await fetchJson<AdminState>(
        `/api/admin/state?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
        { credentials: "same-origin" },
      );
      setAdminState(payload);
      applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
      if (payload.deployment) {
        setDeploymentStatus(payload.deployment);
      }
      if (!window.localStorage.getItem(DIRECTORY_STORAGE_KEY)) {
        setSelectedDirectoryPath(payload.selected_directory_path || "root");
      }
      setSessionError(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Unable to load admin state.");
    }
  }

  async function refreshAuthSession(message?: string) {
    try {
      const payload = await fetchJson<AdminSession>("/api/auth/session", { credentials: "same-origin" });
      if (payload.branding) {
        setBranding(normalizeAdminBranding(payload.branding));
        setBrandingLoaded(true);
      }
      setSession(payload);
      applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
      if (payload.auth_required && !payload.authenticated) {
        setAdminState(null);
      }
      setSessionError(message ?? null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Unable to load session.");
    }
  }

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      return;
    }
    void loadDirectoryState(selectedDirectoryPath);
  }, [session, selectedDirectoryPath]);

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      setUserNotifications({ unread_count: 0, notifications: [] });
      return;
    }
    if (!shellBackgroundPollingEnabled) {
      return;
    }
    let cancelled = false;
    async function loadUserNotifications() {
      try {
        const payload = await fetchJson<UserNotificationResponse>("/api/admin/user-notifications?limit=20", {
          credentials: "same-origin",
        });
        if (!cancelled) {
          setUserNotifications({
            unread_count: payload.unread_count,
            notifications: payload.notifications,
          });
          setUserNotificationsError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setUserNotificationsError(error instanceof Error ? error.message : "Unable to load notifications.");
        }
      }
    }
    void loadUserNotifications();
    const intervalId = window.setInterval(() => {
      void loadUserNotifications();
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [session, shellBackgroundPollingEnabled]);

  async function signIn(username: string, password: string) {
    setAuthBusy(true);
    setSessionError(null);
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = (await response.json()) as AdminSession & { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Sign in failed.");
      }
      if (payload.branding) {
        setBranding(normalizeAdminBranding(payload.branding));
        setBrandingLoaded(true);
      }
      setSession(payload);
      applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
      setAdminState(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Sign in failed.");
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    setAuthBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/auth/logout`, {
        method: "POST",
        credentials: "same-origin",
      });
      const payload = (await response.json()) as AdminSession;
      if (payload.branding) {
        setBranding(normalizeAdminBranding(payload.branding));
        setBrandingLoaded(true);
      }
      setSession(payload);
      setAdminState(null);
      applyPrivilegeLockState(false);
    } finally {
      setAuthBusy(false);
    }
  }

  function startAzureSignIn() {
    if (!session?.azure_sso_login_url) {
      return;
    }
    setAuthBusy(true);
    const loginUrl = new URL(`${API_BASE}${session.azure_sso_login_url}`, window.location.origin);
    const nextPath = nextPathFromHash(window.location.hash);
    if (nextPath) {
      loginUrl.searchParams.set("next", nextPath);
    }
    window.location.assign(loginUrl.toString());
  }

  async function setPrivilegeLock(unlock: boolean) {
    setAuthBusy(true);
    if (!unlock) {
      applyPrivilegeLockState(false);
    }
    try {
      const payload = await fetchJson<PrivilegedOperationsResponse>(
        `/api/admin/privileged/${unlock ? "unlock" : "lock"}`,
        {
          method: "POST",
          credentials: "same-origin",
        },
      );
      applyPrivilegeLockState(Boolean(payload.privileged_operations?.unlocked));
      setSessionError(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Unable to update privileged operation lock state.");
    } finally {
      setAuthBusy(false);
    }
  }

  async function refreshUserNotifications() {
    try {
      const payload = await fetchJson<UserNotificationResponse>("/api/admin/user-notifications?limit=20", {
        credentials: "same-origin",
      });
      setUserNotifications({
        unread_count: payload.unread_count,
        notifications: payload.notifications,
      });
      setUserNotificationsError(null);
    } catch (error) {
      setUserNotificationsError(error instanceof Error ? error.message : "Unable to load notifications.");
    }
  }

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      return;
    }
    function handleNotificationMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) {
        return;
      }
      const payload = event.data;
      if (!payload || typeof payload !== "object") {
        return;
      }
      if ((payload as { type?: unknown }).type === "admin-user-notifications:changed") {
        void refreshUserNotifications();
      }
    }
    window.addEventListener("message", handleNotificationMessage);
    return () => window.removeEventListener("message", handleNotificationMessage);
  }, [session]);

  async function markUserNotificationsRead(notificationIds: string[] | null) {
    setUserNotificationsBusy(true);
    try {
      const payload = await fetchJson<UserNotificationResponse>("/api/admin/user-notifications/read", {
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify(
          notificationIds === null
            ? { all: true }
            : { notification_ids: notificationIds },
        ),
      });
      setUserNotifications({
        unread_count: payload.unread_count,
        notifications: payload.notifications,
      });
      setUserNotificationsError(null);
    } catch (error) {
      setUserNotificationsError(error instanceof Error ? error.message : "Unable to update notifications.");
      await refreshUserNotifications();
    } finally {
      setUserNotificationsBusy(false);
    }
  }

  function openUserNotification(notification: UserNotification) {
    void markUserNotificationsRead([notification.id]);
    setUserNotificationsOpen(false);
    const targetUrl = notification.target_url?.trim();
    if (!targetUrl) {
      return;
    }
    if (targetUrl.startsWith("#")) {
      window.location.hash = targetUrl.slice(1);
      return;
    }
    window.location.assign(targetUrl);
  }

  const currentUserRole = adminState?.role ?? session?.role ?? null;
  const currentUserRootPermissions = normalizePermissionList(session?.effective_permissions, session?.permissions);
  const currentUserPermissions = normalizePermissionList(session?.permissions, adminState?.permissions);
  const currentUserEffectivePermissions = normalizePermissionList(
    session?.effective_permissions,
    adminState?.effective_permissions,
    currentUserPermissions,
  );
  const currentUserPermissionSet = new Set(currentUserEffectivePermissions);
  const currentUserRootPermissionSet = new Set(currentUserRootPermissions);
  const isOwner = currentUserRole === "admin" || currentUserPermissionSet.has("admin.owner");
  const hasPermission = (permission: string) => isOwner || currentUserPermissionSet.has(permission);
  const hasRootPermission = (permission: string) =>
    currentUserRootPermissionSet.has("admin.owner") || currentUserRootPermissionSet.has(permission);
  const canReadWorkflows = isOwner || [...WORKFLOW_READ_PERMISSIONS].some((permission) => hasPermission(permission));
  const canReadWorkflowRuns = hasPermission(WORKFLOW_RUN_READ_PERMISSION);
  const canWriteWorkflows = hasPermission(WORKFLOW_WRITE_PERMISSION);
  const canManageWorkflowPacks = hasRootPermission(WORKFLOW_PACKS_MANAGE_PERMISSION);
  const canControlWorkflowRuns = hasPermission(WORKFLOW_RUN_CONTROL_PERMISSION);
  const canManageUsers =
    isOwner ||
    [...USER_MANAGEMENT_PERMISSIONS].some((permission) => hasPermission(permission)) ||
    Boolean(adminState?.can_manage_users ?? session?.can_manage_users);
  const workflowFocus = renderedTab === "workflow";
  const currentUserId = (
    session?.username?.trim()
    || session?.display_name?.trim()
    || session?.session_id?.trim()
    || "anonymous"
  );
  const currentUserLabel = (
    session?.display_name?.trim()
    || session?.username?.trim()
    || session?.session_id?.trim()
    || "anonymous"
  );
  const headerLogoUrl = brandingLoaded
    ? themedLogoUrl(branding.header_logo, theme, logoLight, logoDark)
    : null;
  const headerLogoClassName = `admin-shell-logo${
    branding.header_logo_size === "wordmark" ? " admin-shell-logo-wordmark" : ""
  }`;
  const headerTitleClassName = branding.header_title_visible
    ? undefined
    : "admin-shell-header-title-hidden";

  useEffect(() => {
    function handleHashChange() {
      if (suppressNextHashChangeRef.current) {
        suppressNextHashChangeRef.current = false;
        return;
      }
      const nextTab = currentTab(window.location.hash);
      if (nextTab === selectedTab) {
        return;
      }
      if (renderedTab === "workflow" && nextTab !== "workflow" && workflowHasUnsavedChanges) {
        if (!window.confirm("Workflow has unsaved changes. Discard them and continue?")) {
          suppressNextHashChangeRef.current = true;
          window.location.hash = "workflow";
          setSelectedTab("workflow");
          return;
        }
      }
      setSelectedTab(nextTab);
    }
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [renderedTab, selectedTab, workflowHasUnsavedChanges]);

  const adminUiState = {
    privilegedUnlocked: privilegeUnlocked,
    authBusy,
    authMode: session?.auth_mode ?? "none",
    authRequired: session?.auth_required ?? false,
    authenticated: session?.authenticated ?? false,
    directoryScopedConfigEnabled: adminState?.directory_scoped_config_enabled ?? false,
    currentUserId,
    currentUserLabel,
    currentUserRole,
    currentUserPermissions,
    currentUserEffectivePermissions,
    canReadWorkflows,
    canReadWorkflowRuns,
    canWriteWorkflows,
    canManageWorkflowPacks,
    canControlWorkflowRuns,
    canManageUsers,
    selectedDirectoryPath,
    selectedDirectoryParentPath,
    directoryTree,
    directoryStateLoading,
    directoryStateError,
    directoryPickerOpen,
    openDirectoryPicker: () => setDirectoryPickerOpen(true),
    closeDirectoryPicker: () => setDirectoryPickerOpen(false),
    setSelectedDirectoryPath: (path: string) => setSelectedDirectoryPath(path || "root"),
    workflowHasUnsavedChanges,
    setWorkflowHasUnsavedChanges,
    refreshDirectoryState: async () => loadDirectoryState(selectedDirectoryPath),
    refreshAdminState,
    refreshAuthSession,
  };

  function navigateToTab(nextTab: AdminTab) {
    suppressNextHashChangeRef.current = true;
    window.location.hash = nextTab;
    setSelectedTab(nextTab);
  }

  function confirmDiscardWorkflowChanges() {
    if (!workflowHasUnsavedChanges || renderedTab !== "workflow") {
      return true;
    }
    return window.confirm("Workflow has unsaved changes. Discard them and continue?");
  }

  if (session === null) {
    return (
      <div className="admin-login-shell">
        <div className="admin-login-stage">
          <LoginBrandBar
            theme={theme}
            branding={branding}
            brandingLoaded={brandingLoaded}
            onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
          />
          <DeploymentStatusBanner status={deploymentStatus} />
          <div className="admin-login-card">
            <div className="eyebrow">{branding.header_title}</div>
            <h1>Loading admin shell</h1>
            <p>{sessionError ?? "Checking authentication and bootstrapping the UI."}</p>
          </div>
        </div>
      </div>
    );
  }

  if (session.auth_required && !session.authenticated) {
    return (
      <LoginScreen
        session={session}
        theme={theme}
        branding={branding}
        brandingLoaded={brandingLoaded}
        onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
        deploymentStatus={deploymentStatus}
        error={sessionError}
        loading={authBusy}
        onSubmit={signIn}
        onAzureSignIn={startAzureSignIn}
      />
    );
  }

  return (
    <AdminUiContext.Provider value={adminUiState}>
      <div className={`admin-shell${workflowFocus ? " workflow-focus" : ""}${workflowSidebarCollapsed ? " workflow-sidebar-collapsed" : ""}`}>
        <header className="admin-shell-header">
          <div className="admin-shell-brand">
            <div className="admin-shell-brand-lockup">
              <div className="admin-shell-brand-copy">
                <div className="admin-shell-title-row">
                  {headerLogoUrl ? (
                    <img className={headerLogoClassName} src={headerLogoUrl} alt={branding.header_logo.alt} />
                  ) : (
                    <span className={`${headerLogoClassName} admin-shell-logo-placeholder`} aria-hidden="true" />
                  )}
                  <h1 className={headerTitleClassName}>{branding.header_title}</h1>
                </div>
                <p className="admin-shell-active-description">{activeTab.description}</p>
              </div>
            </div>
          </div>
          <div className="admin-shell-actions">
            <div className="admin-shell-action-group">
              <button
                type="button"
                className="admin-shell-button admin-shell-button-muted admin-theme-toggle-button"
                onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
                disabled={authBusy}
              >
                {theme === "dark" ? "Light mode" : "Dark mode"}
              </button>
              <button
                type="button"
                className="admin-shell-button admin-shell-button-muted admin-directory-picker-button"
                onClick={() => setDirectoryPickerOpen(true)}
                disabled={authBusy}
              >
                Choose directory
              </button>
            </div>
            <div className="admin-shell-info-group">
              <AdminUserNotifications
                open={userNotificationsOpen}
                busy={userNotificationsBusy}
                error={userNotificationsError}
                state={userNotifications}
                onToggle={() => setUserNotificationsOpen((current) => !current)}
                onClose={() => setUserNotificationsOpen(false)}
                onMarkAllRead={() => void markUserNotificationsRead(null)}
                onNotificationClick={openUserNotification}
              />
              <div className="admin-directory-context">
                <span className="section-label">Directory</span>
                <strong>{selectedDirectoryPath}</strong>
              </div>
              <div className="admin-identity">
                <span>{session.display_name ?? session.username ?? "local operator"}</span>
                <span className={`admin-privilege ${privilegeUnlocked ? "unlocked" : ""}`}>
                  {privilegeUnlocked ? "Privileges unlocked" : "Privileges locked"}
                </span>
              </div>
            </div>
            <div className="admin-shell-action-group">
              <button
                type="button"
                className="admin-shell-button admin-privilege-toggle-button"
                onClick={() => void setPrivilegeLock(!privilegeUnlocked)}
                disabled={authBusy || adminState === null}
              >
                {privilegeUnlocked ? "Lock" : "Unlock"}
              </button>
              {session.auth_mode !== "none" ? (
                <button
                  type="button"
                  className="admin-shell-button admin-shell-button-muted admin-signout-button"
                  onClick={() => void signOut()}
                  disabled={authBusy}
                >
                  Sign out
                </button>
              ) : null}
            </div>
          </div>
        </header>

        <ChatLauncher
          enabled={Boolean(session.authenticated || !session.auth_required)}
          assistantEnabled={Boolean(adminState?.assistant?.enabled)}
          showWhenDisabled={Boolean(adminState?.assistant?.show_launcher_when_disabled)}
        />

        <div className="admin-shell-layout">
          <nav className={`admin-shell-nav${workflowSidebarCollapsed ? " collapsed" : ""}`} aria-label="Admin sections">
            <div className="admin-shell-nav-header">
              <div className="admin-shell-nav-header-row">
                <div className="section-label">Sections</div>
                <Tooltip content={workflowSidebarCollapsed ? "Expand the left navigation sections." : "Collapse the left navigation sections."}>
                  <button
                    type="button"
                    className="admin-shell-nav-toggle"
                    onClick={() => setWorkflowSidebarCollapsed((current) => !current)}
                    disabled={authBusy}
                    aria-label={workflowSidebarCollapsed ? "Expand sections" : "Collapse sections"}
                  >
                    <span aria-hidden="true">≡</span>
                  </button>
                </Tooltip>
              </div>
              <p>Operational views for machines, policy, reviews, and workflow state.</p>
            </div>
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                className={`admin-shell-tab ${tab.key === renderedTab ? "active" : ""}`}
                onClick={() => {
                  if (tab.key === renderedTab) {
                    return;
                  }
                  if (!confirmDiscardWorkflowChanges()) {
                    return;
                  }
                  navigateToTab(tab.key);
                }}
              >
                <span className="admin-shell-tab-label admin-shell-tab-label-full">{tab.label}</span>
                <span className="admin-shell-tab-label admin-shell-tab-label-short">{tab.shortLabel}</span>
                <small>{tab.description}</small>
              </button>
            ))}
          </nav>
          <main className="admin-shell-main">
            <DeploymentStatusBanner status={deploymentStatus} />
            {sessionError ? <div className="admin-shell-alert">{sessionError}</div> : null}
            {renderedTab === "dashboard" ? (
              <OperationsDashboard />
            ) : renderedTab === "workflow" ? (
              <div className="admin-workflow-panel">
                <WorkflowEditorApp />
              </div>
            ) : renderedTab === "vms" ? (
              <VmsPage />
            ) : renderedTab === "agents" ? (
              <AgentsPage />
            ) : renderedTab === "skills" ? (
              <SkillsPage />
            ) : renderedTab === "issues" ? (
              <IssuesPage />
            ) : renderedTab === "configuration" ? (
              <ConfigurationScreen />
            ) : renderedTab === "reports" ? (
              <ReportsPage />
            ) : renderedTab === "users" ? (
              <UsersPage />
            ) : renderedTab === "workers" ? (
              <WorkersScreen />
            ) : renderedTab === "sessions" ? (
              <SessionsScreen />
            ) : renderedTab === "tool-visibility" ? (
              <ToolVisibilityPage />
            ) : renderedTab === "mcp-tools" ? (
              <McpToolsPage />
            ) : renderedTab === "approvals" ? (
              <ApprovalsScreen />
            ) : renderedTab === "artifacts" ? (
              <ArtifactsPage />
            ) : renderedTab === "addons" ? (
              <AddonsPage />
            ) : renderedTab === "tool-calls" ? (
              <ToolCallsPage />
            ) : activeTab.key.startsWith("addon:") ? (
              <AddonView
                tab={activeTab}
                selectedDirectoryPath={selectedDirectoryPath}
                privilegedUnlocked={privilegeUnlocked}
              />
            ) : (
              <PlaceholderView title={activeTab.label} description={activeTab.description} />
            )}
          </main>
        </div>
      </div>
      <DirectoryChooserModal
        open={directoryPickerOpen}
        loading={directoryStateLoading}
        error={directoryStateError}
        selectedPath={selectedDirectoryPath}
        privilegedUnlocked={privilegeUnlocked}
        directoryScopedConfigEnabled={adminState?.directory_scoped_config_enabled ?? false}
        tree={directoryTree}
        onClose={() => setDirectoryPickerOpen(false)}
        onRefreshState={(path) => loadDirectoryState(path ?? selectedDirectoryPath)}
        onSelect={(path) => {
          if (path !== selectedDirectoryPath && renderedTab === "workflow" && workflowHasUnsavedChanges) {
            if (!window.confirm("Workflow has unsaved changes. Discard them and continue?")) {
              return;
            }
          }
          setSelectedDirectoryPath(path);
          setDirectoryPickerOpen(false);
        }}
      />
    </AdminUiContext.Provider>
  );
}
