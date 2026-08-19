"use client";

import { useEffect } from "react";

export default function Home() {
  useEffect(() => { window.location.replace("/index.html"); }, []);
  return <main style={{color: "#c9a65d", fontFamily: "system-ui", padding: 32}}>Opening VANTERA HQ…</main>;
}
