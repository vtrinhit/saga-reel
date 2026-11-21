/**
 * Từ điển nhãn người đọc được cho trường shot/cắt (thay mã nghề EWS/MCU/… mà người dùng không hiểu). Danh sách giá trị
 * vẫn lấy từ `meta.enums` (server là nguồn sự thật); ở đây chỉ có nhãn tiếng Việt + mô tả ngắn. Giá trị lạ (server thêm
 * mới) giữ nguyên mã làm nhãn.
 */
import type { MetaEnums } from './episodeView';

export type ShotKey =
  | 'shot_size' | 'angle' | 'camera_move' | 'lens' | 'composition' | 'eyeline' | 'screen_dir'
  | 'axis_side' | 'cut_in' | 'cut_out';

export interface ShotOption {
  value: string;
  /** Nhãn chính tiếng Việt: "Toàn cảnh xa". */
  label: string;
  /** Mô tả ngắn, nói cái người xem thấy: "thấy cả bối cảnh". */
  hint: string;
  /** Mã nghề, hiện chữ nhỏ phụ: "EWS". Rỗng khi mã chẳng giúp gì. */
  code: string;
}

export const SHOT_KEY_LABEL: Record<ShotKey, string> = {
  shot_size: 'Cỡ cảnh',
  angle: 'Góc máy',
  camera_move: 'Chuyển động máy',
  lens: 'Ống kính',
  composition: 'Bố cục',
  eyeline: 'Hướng nhìn',
  screen_dir: 'Hướng chuyển động',
  axis_side: 'Phía trục',
  cut_in: 'Cắt vào',
  cut_out: 'Cắt ra',
};

/** Trường hiện ngay trong trình sửa; còn lại nằm trong "Nâng cao". */
export const PRIMARY_SHOT_KEYS: ShotKey[] = ['shot_size', 'camera_move'];
export const ADVANCED_SHOT_KEYS: ShotKey[] = ['angle', 'lens', 'composition', 'eyeline', 'screen_dir', 'axis_side', 'cut_in', 'cut_out'];

type Row = [value: string, label: string, hint: string, code?: string];

const CUTS: Row[] = [
  ['cut', 'Cắt thẳng', 'chuyển ngay sang cảnh mới'],
  ['match_action', 'Khớp hành động', 'nối giữa một động tác'],
  ['match_graphic', 'Khớp hình', 'hai khung có hình dạng giống nhau'],
  ['j_cut', 'Tiếng vào trước', 'nghe cảnh sau trước khi thấy', 'J-cut'],
  ['l_cut', 'Tiếng kéo dài', 'tiếng cảnh trước còn vang sang', 'L-cut'],
  ['smash', 'Cắt đột ngột', 'đổi nhịp mạnh, gây giật mình'],
  ['cutaway', 'Cắt chen', 'chen một chi tiết rồi quay lại'],
  ['dissolve', 'Hoà tan', 'hai cảnh mờ chồng vào nhau'],
];

const TABLE: Record<ShotKey, Row[]> = {
  shot_size: [
    ['ews', 'Toàn cảnh xa', 'thấy cả bối cảnh, người rất nhỏ', 'EWS'],
    ['ws', 'Toàn cảnh', 'thấy bối cảnh và cả người', 'WS'],
    ['fs', 'Toàn thân', 'từ đầu tới chân', 'FS'],
    ['mws', 'Trung toàn', 'từ đầu gối trở lên', 'MWS'],
    ['ms', 'Trung cảnh', 'từ thắt lưng trở lên', 'MS'],
    ['mcu', 'Cận trung', 'từ ngực trở lên', 'MCU'],
    ['cu', 'Cận mặt', 'khuôn mặt, biểu cảm', 'CU'],
    ['ecu', 'Đặc tả', 'một chi tiết: mắt, môi, bàn tay', 'ECU'],
    ['insert', 'Cận đồ vật', 'chèn chi tiết đồ vật', 'Insert'],
  ],
  angle: [
    ['eye', 'Ngang tầm mắt', 'trung tính, tự nhiên'],
    ['low', 'Góc thấp', 'nhìn lên, nhân vật lớn mạnh'],
    ['high', 'Góc cao', 'nhìn xuống, nhân vật nhỏ bé'],
    ['dutch', 'Khung nghiêng', 'gây cảm giác bất an', 'Dutch'],
    ['ots', 'Qua vai', 'nhìn qua vai người đối diện', 'OTS'],
    ['pov', 'Mắt nhân vật', 'thấy đúng như nhân vật thấy', 'POV'],
    ['overhead', 'Từ trên xuống', 'nhìn thẳng từ trên cao'],
  ],
  camera_move: [
    ['static', 'Đứng yên', 'máy không chuyển động'],
    ['push_in', 'Đẩy vào', 'tiến lại gần, tăng cảm xúc'],
    ['pull_out', 'Kéo ra', 'lùi xa, mở bối cảnh'],
    ['pan_l', 'Lia trái', 'máy xoay sang trái'],
    ['pan_r', 'Lia phải', 'máy xoay sang phải'],
    ['tilt_u', 'Ngửa lên', 'máy ngẩng dần lên'],
    ['tilt_d', 'Cúi xuống', 'máy cúi dần xuống'],
    ['truck_l', 'Trượt trái', 'máy dịch ngang sang trái'],
    ['truck_r', 'Trượt phải', 'máy dịch ngang sang phải'],
    ['track', 'Bám theo', 'đi theo nhân vật'],
    ['arc', 'Vòng quanh', 'đi vòng quanh nhân vật'],
    ['crane_up', 'Nâng máy lên', 'máy nâng dần lên cao'],
    ['crane_down', 'Hạ máy xuống', 'máy hạ dần xuống thấp'],
    ['handheld', 'Cầm tay', 'rung nhẹ, chân thực'],
    ['whip_pan', 'Lia vụt', 'lia rất nhanh để chuyển cảnh'],
    ['rack_focus', 'Chuyển nét', 'đổi chỗ lấy nét trong khung'],
  ],
  lens: [
    ['24mm', 'Góc rộng', 'rộng, hơi méo ở rìa', '24mm'],
    ['35mm', 'Kể chuyện', 'gần với mắt người', '35mm'],
    ['50mm', 'Tự nhiên', 'ít méo, cân đối', '50mm'],
    ['85mm', 'Chân dung', 'nền mờ, tập trung vào mặt', '85mm'],
    ['anamorphic', 'Điện ảnh', 'khung rộng, loé sáng ngang', 'Anamorphic'],
  ],
  composition: [
    ['center', 'Giữa khung', 'chủ thể ở chính giữa'],
    ['thirds_l', 'Lệch trái', 'chủ thể ở đường 1/3 bên trái'],
    ['thirds_r', 'Lệch phải', 'chủ thể ở đường 1/3 bên phải'],
    ['symmetry', 'Đối xứng', 'hai nửa khung cân nhau'],
    ['leading_lines', 'Đường dẫn', 'đường nét dẫn mắt tới chủ thể'],
    ['frame_in_frame', 'Khung trong khung', 'cửa, ô cửa bao quanh chủ thể'],
    ['negative_space', 'Nhiều khoảng trống', 'chủ thể nhỏ giữa không gian'],
    ['headroom_tight', 'Sát đầu', 'cắt sát đỉnh đầu'],
  ],
  eyeline: [
    ['screen_left', 'Nhìn sang trái', 'ánh mắt hướng mép trái'],
    ['screen_right', 'Nhìn sang phải', 'ánh mắt hướng mép phải'],
    ['to_camera', 'Nhìn vào máy', 'nhìn thẳng người xem'],
    ['down', 'Nhìn xuống', 'cúi mắt'],
    ['up', 'Nhìn lên', 'ngước mắt'],
  ],
  screen_dir: [
    ['L→R', 'Trái sang phải', 'di chuyển về mép phải'],
    ['R→L', 'Phải sang trái', 'di chuyển về mép trái'],
    ['toward_cam', 'Về phía máy', 'tiến lại gần người xem'],
    ['away_cam', 'Ra xa máy', 'đi xa khỏi người xem'],
    ['none', 'Không di chuyển', 'đứng yên trong khung'],
  ],
  axis_side: [
    ['A', 'Phía A', 'giữ cùng phía với clip trước để không vượt trục'],
    ['B', 'Phía B', 'phía bên kia trục 180°'],
  ],
  cut_in: CUTS,
  cut_out: CUTS,
};

const BY_VALUE = Object.fromEntries(
  (Object.keys(TABLE) as ShotKey[]).map((k) => [
    k,
    new Map(TABLE[k].map(([value, label, hint, code]) => [value, { value, label, hint, code: code ?? '' }])),
  ]),
) as Record<ShotKey, Map<string, ShotOption>>;

/** Nhãn của một giá trị; giá trị lạ → chính mã đó, không mô tả. */
export function shotOption(key: ShotKey, value: string): ShotOption {
  return BY_VALUE[key].get(value) ?? { value, label: value, hint: '', code: '' };
}

/** "Trung cảnh" (rỗng khi chưa đặt). */
export function shotText(key: ShotKey, value: string | null | undefined): string {
  return value ? shotOption(key, value).label : '';
}

/** Nhãn một dòng cho `<option>`/ô bảng: "Trung cảnh — từ thắt lưng trở lên". */
export function shotOptionText(o: ShotOption): string {
  return o.hint ? `${o.label} — ${o.hint}` : o.label;
}

/** Danh sách lựa chọn theo thứ tự `meta.enums` (nếu có), không thì bảng tĩnh. */
export type ShotMeta = MetaEnums & { cut?: Partial<Record<'axis_side' | 'cut_in' | 'cut_out', string[]>> };

export function shotOptions(key: ShotKey, meta?: ShotMeta | null): ShotOption[] {
  const fromServer =
    (key === 'axis_side' || key === 'cut_in' || key === 'cut_out'
      ? meta?.cut?.[key]
      : meta?.shot?.[key as keyof NonNullable<MetaEnums['shot']>]) ?? null;
  const values = fromServer?.length ? fromServer : TABLE[key].map((r) => r[0]);
  return values.map((v) => shotOption(key, v));
}

/**
 * Hình minh hoạ cỡ cảnh: tỉ lệ phóng một hình người trong khung (1 = vừa toàn thân) và độ dịch đầu lên mép trên.
 * `insert` dùng hình đồ vật.
 */
export const SHOT_SIZE_PICTO: Record<string, { scale: number; lift: number } | 'object'> = {
  ews: { scale: 0.22, lift: 0 },
  ws: { scale: 0.45, lift: 0 },
  fs: { scale: 0.9, lift: 0 },
  mws: { scale: 1.35, lift: 0.18 },
  ms: { scale: 1.9, lift: 0.36 },
  mcu: { scale: 2.6, lift: 0.5 },
  cu: { scale: 4, lift: 0.66 },
  ecu: { scale: 7.5, lift: 0.8 },
  insert: 'object',
};
