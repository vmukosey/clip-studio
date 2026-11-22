export interface StudioClip {
  id: string;
  title: string;
  status: string;
  duration: number;
  available: boolean;
  srt: boolean;
}

export interface StudioJob {
  id: string;
  source: string;
  status: string;
  created_at: string;
  clips: StudioClip[];
}

export interface StudioProjectRecord {
  id: string;
  name: string;
  revision: number;
  document: import('../lib/types').Project;
  updated_at: string;
}

export interface StudioRender {
  id: string;
  project_id: string;
  revision: number;
  duration: number;
  url: string;
  transcript_status: string;
  transcript: Array<{ start: number; end: number; text: string }>;
  transcript_error: string | null;
  created_at: string;
}

export interface StudioVoiceover {
  id: string;
  render_id: string;
  mode: string;
  prompt: string;
  status: string;
  regions: Array<{ start: number; end: number }>;
  lines: Array<{ start: number; end: number; text: string }>;
  artifacts: { wav?: string; mp4?: string };
  error: string | null;
  created_at: string;
}
