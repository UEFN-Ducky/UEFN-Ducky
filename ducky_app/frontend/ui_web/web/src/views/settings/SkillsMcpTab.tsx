import { SkillPackStudio } from "../../skill-pack-studio";
import { McpPluginsSection } from "./McpPluginsSection";

export type SkillsMcpSectionTab = "skills" | "mcps";

interface SkillsMcpTabProps {
  sectionTab: SkillsMcpSectionTab;
}

export function SkillsMcpTab({ sectionTab }: SkillsMcpTabProps) {
  return (
    <div className="skills-mcp-tab-shell">
      {sectionTab === "skills" ? <SkillPackStudio sectionTab={sectionTab} /> : <McpPluginsSection />}
    </div>
  );
}
