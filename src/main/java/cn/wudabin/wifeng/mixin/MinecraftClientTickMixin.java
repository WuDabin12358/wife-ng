package cn.wudabin.wifeng.mixin;

import cn.wudabin.wifeng.WifeNgClient;
import net.minecraft.client.MinecraftClient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MinecraftClient.class)
public abstract class MinecraftClientTickMixin {
    @Inject(method = "tick", at = @At("TAIL"))
    private void wifeNg$afterTick(CallbackInfo callbackInfo) {
        WifeNgClient.onEndClientTick((MinecraftClient) (Object) this);
    }
}
