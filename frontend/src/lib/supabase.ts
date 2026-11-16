import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const isLocalMode = process.env.NEXT_PUBLIC_APP_MODE === "local";

export const supabase = isLocalMode ? null : createClient(supabaseUrl, supabaseAnonKey);
