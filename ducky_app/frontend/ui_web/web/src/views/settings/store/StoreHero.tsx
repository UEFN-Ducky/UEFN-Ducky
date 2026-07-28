import { useEffect, useState } from "react";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
import { itemInitials, type HeroSlide } from "./storeData";

type Props = {
  slides: HeroSlide[];
  onOpen: (item: DuckyOSStoreItemDto) => void;
};

/** Auto-rotating promo banner (5s) with dot navigation; each slide opens its item. */
export function StoreHero({ slides, onOpen }: Props) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (slides.length < 2) return;
    const timer = setInterval(() => {
      setIndex((prev) => (prev + 1) % slides.length);
    }, 5000);
    return () => clearInterval(timer);
  }, [slides.length]);

  useEffect(() => {
    if (index >= slides.length && slides.length > 0) setIndex(0);
  }, [slides.length, index]);

  if (!slides.length) return null;

  return (
    <div className="ds-hero">
      {slides.map((slide, i) => {
        const item = slide.item;
        const active = i === index;
        return (
          <button
            key={item.slug || i}
            type="button"
            className={`ds-hero-slide ds-hero-slide--${slide.variant}${active ? " is-active" : ""}`}
            aria-hidden={!active}
            tabIndex={active ? 0 : -1}
            onClick={() => onOpen(item)}
          >
            <div className="ds-hero-art" aria-hidden>
              {item.icon_data_url ? (
                <img src={item.icon_data_url} alt="" draggable={false} />
              ) : (
                <span className="ds-hero-art-fallback">{itemInitials(item)}</span>
              )}
            </div>
            {/* Only active slide mounts copy — inactive opacity:0 still painted during crossfade. */}
            {active ? (
              <div className="ds-hero-copy">
                <span className={`ds-hero-tag ds-hero-tag--${slide.variant}`}>{slide.tag}</span>
                <h2 className="ds-hero-title">{item.name || item.slug}</h2>
                <p className="ds-hero-desc">{item.description || ""}</p>
              </div>
            ) : null}
          </button>
        );
      })}
      <div className="ds-hero-dots">
        {slides.map((_, i) => (
          <button
            key={i}
            type="button"
            className={`ds-hero-dot${i === index ? " is-active" : ""}`}
            aria-label={`Slide ${i + 1}`}
            onClick={() => setIndex(i)}
          />
        ))}
      </div>
    </div>
  );
}
