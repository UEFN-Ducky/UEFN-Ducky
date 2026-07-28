import { Modal } from "../Modal";
import { Icons } from "../../icons/Icons";
import { DuckyAvatar } from "./DuckyAvatars";
import { duckyAccentClass, duckyTagline } from "./duckyProfileForm";
import type { AgentProfileDto } from "../../types/panel";

const TEMPLATE_AVATAR_SIZE = 68;

interface NewDuckyModalProps {
  open: boolean;
  templates: AgentProfileDto[];
  disabled?: boolean;
  onClose: () => void;
  /** Start from a blank slate. */
  onBlank: () => void;
  /** Start from an existing ducky/template. */
  onPick: (profile: AgentProfileDto) => void;
}

/**
 * "New Ducky" picker rendered in the app's shared Modal — a blank starting
 * point plus a grid of duck templates to fork from. Deliberately reuses the
 * `.ducky-card` grid styling so the templates read the same as the library.
 */
export function NewDuckyModal({ open, templates, disabled = false, onClose, onBlank, onPick }: NewDuckyModalProps) {
  return (
    <Modal open={open} onClose={onClose} title="Create New Ducky" width={640} hideHeader floatingClose>
      <div className="new-ducky-modal">
        <header className="new-ducky-modal-head">
          <h2 className="new-ducky-modal-title">Create New Ducky</h2>
          <p className="new-ducky-modal-sub">Start fresh or fork a specialized template.</p>
        </header>

        <button type="button" className="new-ducky-blank" onClick={onBlank} disabled={disabled}>
          <span className="new-ducky-blank-icon" aria-hidden>
            <Icons.Plus />
          </span>
          <span className="new-ducky-blank-text">
            <span className="new-ducky-blank-title">Custom Ducky</span>
            <span className="new-ducky-blank-desc">
              Start with a blank slate and build your own personality and toolset.
            </span>
          </span>
          <span className="new-ducky-blank-caret" aria-hidden>
            <Icons.ChevronRight />
          </span>
        </button>

        {templates.length > 0 ? (
          <section className="new-ducky-templates">
            <h3 className="new-ducky-templates-label">Or choose a template</h3>
            <div className="ducky-card-grid ducky-card-grid--modal">
              {templates.map((profile) => (
                <button
                  key={profile.id}
                  type="button"
                  className={`ducky-card ducky-card--pick ${duckyAccentClass(profile.id)}`}
                  onClick={() => onPick(profile)}
                  disabled={disabled}
                  title={profile.name}
                >
                  <span className="ducky-card-glow" aria-hidden />
                  <span className="ducky-card-avatar-wrap">
                    <DuckyAvatar styleId={profile.ducky_style} size={TEMPLATE_AVATAR_SIZE} />
                  </span>
                  <span className="ducky-card-meta">
                    <span className="ducky-card-name">{profile.name}</span>
                    <span className="ducky-card-tag">{duckyTagline(profile)}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </Modal>
  );
}
