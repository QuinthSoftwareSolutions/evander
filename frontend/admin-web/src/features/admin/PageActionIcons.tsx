import {
  Ban,
  BarChart3,
  BellRing,
  CheckCircle2,
  CirclePause,
  CirclePlay,
  Compass,
  Copy,
  Cpu,
  Download,
  Eraser,
  FolderPlus,
  History,
  Laptop2,
  MessageCircle,
  Pencil,
  Play,
  Puzzle,
  RefreshCw,
  Save,
  Timer,
  SendHorizontal,
  Settings2,
  ShieldCheck,
  ShieldX,
  Square,
  SquareX,
  Trash2,
  Upload,
  Users,
  WandSparkles,
  Workflow,
  Wrench,
  ChevronLeft,
  ChevronRight,
  Plus,
  Code2,
  Server,
  Package,
  UserRoundCog,
  Ticket,
  Eye,
  PackageOpen,
  PowerOff,
} from "lucide-react";

const DEFAULT_ICON_PROPS = { size: 16, strokeWidth: 1.9 };

export function SaveIcon() {
  return <Save {...DEFAULT_ICON_PROPS} />;
}

export function CopyIcon() {
  return <Copy {...DEFAULT_ICON_PROPS} />;
}

export function PlusIcon() {
  return <Plus {...DEFAULT_ICON_PROPS} />;
}

export function HistoryIcon() {
  return <History {...DEFAULT_ICON_PROPS} />;
}

export function GenerateWorkflowIcon() {
  return <WandSparkles {...DEFAULT_ICON_PROPS} />;
}

export function DownloadIcon() {
  return <Download {...DEFAULT_ICON_PROPS} />;
}

export function UploadIcon() {
  return <Upload {...DEFAULT_ICON_PROPS} />;
}

export function EditIcon() {
  return <Pencil {...DEFAULT_ICON_PROPS} />;
}

export function DeleteIcon() {
  return <Trash2 {...DEFAULT_ICON_PROPS} />;
}

export function RefreshIcon() {
  return <RefreshCw {...DEFAULT_ICON_PROPS} />;
}

export function StartIcon() {
  return <Play {...DEFAULT_ICON_PROPS} />;
}

export function StopIcon() {
  return <Square {...DEFAULT_ICON_PROPS} />;
}

export function ExtendIcon() {
  return <Timer {...DEFAULT_ICON_PROPS} />;
}

export function RevokeIcon() {
  return <ShieldX {...DEFAULT_ICON_PROPS} />;
}

export function CleanupIcon() {
  return <Eraser {...DEFAULT_ICON_PROPS} />;
}

export function SettingsIcon() {
  return <Settings2 {...DEFAULT_ICON_PROPS} />;
}

export function AssistantIcon() {
  return <MessageCircle {...DEFAULT_ICON_PROPS} />;
}

export function CreateVmIcon() {
  return <Laptop2 {...DEFAULT_ICON_PROPS} />;
}

export function DirectoryAddIcon() {
  return <FolderPlus {...DEFAULT_ICON_PROPS} />;
}

export function DirectoryDeleteIcon() {
  return <DeleteIcon />;
}

export function NotificationBellIcon() {
  return <BellRing {...DEFAULT_ICON_PROPS} />;
}

export function VmRunningIcon() {
  return <CirclePlay {...DEFAULT_ICON_PROPS} />;
}

export function VmStoppedIcon() {
  return <CirclePause {...DEFAULT_ICON_PROPS} />;
}

export function PowerIcon() {
  return <PowerOff {...DEFAULT_ICON_PROPS} />;
}

export function ResumeIcon() {
  return <SendHorizontal {...DEFAULT_ICON_PROPS} />;
}

export function EnableIcon() {
  return <CheckCircle2 {...DEFAULT_ICON_PROPS} />;
}

export function DisableIcon() {
  return <Ban {...DEFAULT_ICON_PROPS} />;
}

export function KillIcon() {
  return <SquareX {...DEFAULT_ICON_PROPS} />;
}

export function DestroyIcon() {
  return <DeleteIcon />;
}

export function SkillIcon() {
  return <Puzzle {...DEFAULT_ICON_PROPS} />;
}

export function SteeringIcon() {
  return <Compass {...DEFAULT_ICON_PROPS} />;
}

export function DashboardWorkflowIcon() {
  return <Workflow {...DEFAULT_ICON_PROPS} />;
}

export function DashboardApprovalsIcon() {
  return <ShieldCheck {...DEFAULT_ICON_PROPS} />;
}

export function DashboardSessionsIcon() {
  return <Users {...DEFAULT_ICON_PROPS} />;
}

export function DashboardToolsIcon() {
  return <Code2 {...DEFAULT_ICON_PROPS} />;
}

export function DashboardConfigurationIcon() {
  return <Settings2 {...DEFAULT_ICON_PROPS} />;
}

export function DashboardWorkersIcon() {
  return <Server {...DEFAULT_ICON_PROPS} />;
}

export function DashboardAgentsIcon() {
  return <UserRoundCog {...DEFAULT_ICON_PROPS} />;
}

export function DashboardReportsIcon() {
  return <BarChart3 {...DEFAULT_ICON_PROPS} />;
}

export function DashboardVmsIcon() {
  return <Cpu {...DEFAULT_ICON_PROPS} />;
}

export function DashboardArtifactsIcon() {
  return <PackageOpen {...DEFAULT_ICON_PROPS} />;
}

export function DashboardAddonsIcon() {
  return <Package {...DEFAULT_ICON_PROPS} />;
}

export function DashboardSkillsIcon() {
  return <Wrench {...DEFAULT_ICON_PROPS} />;
}

export function DashboardIssuesIcon() {
  return <Ticket {...DEFAULT_ICON_PROPS} />;
}

export function DashboardToolVisibilityIcon() {
  return <Eye {...DEFAULT_ICON_PROPS} />;
}

export function DashboardMcpToolsIcon() {
  return <Server {...DEFAULT_ICON_PROPS} />;
}

export function DashboardUsersIcon() {
  return <UserRoundCog {...DEFAULT_ICON_PROPS} />;
}

export function DashboardRefreshIcon() {
  return <RefreshCw {...DEFAULT_ICON_PROPS} />;
}

export function DashboardNewerIcon() {
  return <ChevronLeft {...DEFAULT_ICON_PROPS} />;
}

export function DashboardOlderIcon() {
  return <ChevronRight {...DEFAULT_ICON_PROPS} />;
}
