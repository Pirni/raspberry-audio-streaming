const overlayElement = document.getElementById('overlay');
const audioElement = document.getElementById('audio');

function init() {
  initAudioStream();
  initOverlay();
}

function initOverlay() {
  overlayElement.addEventListener('click', () => {
    audioElement.play();
    overlayElement.style.display = 'none';
  }, { once: true });
}

function initAudioStream() {
  const params = new URLSearchParams(document.location.search);

  const streamName = params.get("stream");
  var audioSrc = `/hls/${streamName}/index.m3u8`;

  if (Hls.isSupported()) {
      var hls = new Hls({ autoStartLoad: true });
      hls.loadSource(audioSrc);
      hls.attachMedia(audioElement);
  } else if (audio.canPlayType('application/vnd.apple.mpegurl')) {
      audioElement.src = audioSrc;
  }
}

init();
