import {
  Activity,
  Building2,
  FlaskConical,
  Globe2,
  LayoutDashboard,
  Package,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/fleet", label: "Fleet map", icon: Globe2 },
  { href: "/shipments", label: "Shipments", icon: Package },
  { href: "/carriers", label: "Carriers", icon: Building2 },
  { href: "/predictions", label: "Predictions", icon: Activity },
  { href: "/research", label: "Research", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
