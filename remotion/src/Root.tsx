import React from "react";
import { Composition } from "remotion";

import { KineticText, calculateMetadata as ktMeta } from "./compositions/KineticText";
import { TitleCard, calculateMetadata as tcMeta } from "./compositions/TitleCard";
import { DefinitionCard, calculateMetadata as dcMeta } from "./compositions/DefinitionCard";
import { StatCard, calculateMetadata as scMeta } from "./compositions/StatCard";
import { QuoteCard, calculateMetadata as qcMeta } from "./compositions/QuoteCard";
import { Timeline, calculateMetadata as tlMeta } from "./compositions/Timeline";
import { ListReveal, calculateMetadata as lrMeta } from "./compositions/ListReveal";
import { SplitComparison, calculateMetadata as spMeta } from "./compositions/SplitComparison";
import { CaptionBar, calculateMetadata as cbMeta } from "./compositions/CaptionBar";
import { KineticTypography, calculateMetadata as ktypMeta } from "./compositions/KineticTypography";
import { QuickZoom, calculateMetadata as qzMeta } from "./compositions/QuickZoom";
import { MapHighlight, calculateMetadata as mhMeta } from "./compositions/MapHighlight";
import { FactCounter, calculateMetadata as fcMeta } from "./compositions/FactCounter";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="KineticText"
        component={KineticText}
        calculateMetadata={ktMeta}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ text: "AI is changing everything.", duration_s: 5 }}
      />
      <Composition
        id="TitleCard"
        component={TitleCard}
        calculateMetadata={tcMeta}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ title: "AI Agents 2026", subtitle: "What's next?", duration_s: 5 }}
      />
      <Composition
        id="DefinitionCard"
        component={DefinitionCard}
        calculateMetadata={dcMeta}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ term: "Agent", definition: "A system that perceives its environment and takes actions.", duration_s: 6 }}
      />
      <Composition
        id="StatCard"
        component={StatCard}
        calculateMetadata={scMeta}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ value: "1000", label: "AI Agents deployed daily", duration_s: 5 }}
      />
      <Composition
        id="QuoteCard"
        component={QuoteCard}
        calculateMetadata={qcMeta}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ quote: "AI will be as transformative as electricity.", attribution: "Andrew Ng", duration_s: 6 }}
      />
      <Composition
        id="Timeline"
        component={Timeline}
        calculateMetadata={tlMeta}
        durationInFrames={240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          title: "AI Evolution",
          events: [
            { year: "2020", label: "GPT-3" },
            { year: "2022", label: "ChatGPT", highlight: true },
            { year: "2024", label: "Agents" },
            { year: "2026", label: "Everywhere", highlight: true },
          ],
          duration_s: 8,
        }}
      />
      <Composition
        id="ListReveal"
        component={ListReveal}
        calculateMetadata={lrMeta}
        durationInFrames={240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ title: "Key Benefits", items: ["Faster", "Smarter", "Cheaper"], duration_s: 8 }}
      />
      <Composition
        id="SplitComparison"
        component={SplitComparison}
        calculateMetadata={spMeta}
        durationInFrames={240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Before vs After",
          left_label: "Before AI",
          left_items: ["Manual", "Slow", "Error-prone"],
          right_label: "With AI Agents",
          right_items: ["Automated", "Instant", "Accurate"],
          duration_s: 8,
        }}
      />
      <Composition
        id="CaptionBar"
        component={CaptionBar}
        calculateMetadata={cbMeta}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          words: [
            { word: "This", start_s: 0.0, end_s: 0.4 },
            { word: "is", start_s: 0.4, end_s: 0.7 },
            { word: "a", start_s: 0.7, end_s: 0.9 },
            { word: "caption", start_s: 0.9, end_s: 1.5 },
          ],
          duration_s: 10,
        }}
      />
      <Composition
        id="KineticTypography"
        component={KineticTypography}
        calculateMetadata={ktypMeta}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          words: [
            { word: "AI", start_s: 0.0, end_s: 0.5, emphasis: true },
            { word: "is", start_s: 0.5, end_s: 0.8 },
            { word: "here", start_s: 0.8, end_s: 1.5 },
          ],
          duration_s: 10,
        }}
      />
      <Composition
        id="QuickZoom"
        component={QuickZoom}
        calculateMetadata={qzMeta}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ image_url: "", caption: "Source: Pexels", duration_s: 6 }}
      />
      <Composition
        id="MapHighlight"
        component={MapHighlight}
        calculateMetadata={mhMeta}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          region: "Ấn Độ",
          headline: "Không chỉ là một quốc gia",
          subline: "Mà như một hành tinh riêng",
          callouts: ["1.4 tỷ dân", "Himalaya tới Ấn Độ Dương"],
          marker_label: "INDIA",
          duration_s: 6,
        }}
      />
      <Composition
        id="FactCounter"
        component={FactCounter}
        calculateMetadata={fcMeta}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          fact_number: "01",
          headline: "Một lục địa thu nhỏ",
          detail: "Khí hậu thay đổi cực mạnh chỉ trong một quốc gia",
          tag: "ĐỊA LÝ",
          duration_s: 5,
        }}
      />
    </>
  );
};
