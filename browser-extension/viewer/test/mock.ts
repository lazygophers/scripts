// 脚手架全在公共层，这里只做一次转发，测试文件永远只 import 这一个路径。
export {
  clearChrome,
  installChrome,
  page,
  rejectsWith,
  storageMock,
} from "../../shared/test/mock.ts";
